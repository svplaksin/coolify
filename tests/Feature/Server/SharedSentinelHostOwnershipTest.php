<?php

use App\Actions\Server\DeleteServer;
use App\Actions\Server\StartSentinel;
use App\Actions\Server\StopSentinel;
use App\Jobs\CheckAndStartSentinelJob;
use App\Jobs\ServerCheckJob;
use App\Jobs\ServerConnectionCheckJob;
use App\Jobs\ServerManagerJob;
use App\Models\InstanceSettings;
use App\Models\Application;
use App\Models\Environment;
use App\Models\Project;
use App\Models\StandaloneDocker;
use App\Models\Server;
use App\Models\Team;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Event;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Process;
use Illuminate\Support\Facades\Queue;

uses(RefreshDatabase::class);

beforeEach(function () {
    Server::flushIdentityMap();
    Cache::flush();
    Queue::fake();
    config(['constants.coolify.self_hosted' => true]);
    Carbon::setTestNow('2026-11-10 12:00:00');
    InstanceSettings::forceCreate(['id' => 0, 'instance_timezone' => 'UTC']);

    $team = Team::factory()->create();
    $this->owner = Server::factory()->create([
        'id' => 0,
        'team_id' => $team->id,
        'sentinel_updated_at' => now(),
    ]);
    $this->alias = Server::factory()->create([
        'id' => 1,
        'team_id' => $team->id,
        'sentinel_updated_at' => now()->subMinutes(20),
    ]);
    foreach ([$this->owner, $this->alias] as $server) {
        $server->settings->forceFill([
            'is_sentinel_enabled' => true,
            'is_reachable' => true,
            'is_usable' => true,
            'server_timezone' => 'UTC',
        ])->saveQuietly();
    }
    Server::flushIdentityMap();
});

afterEach(function () {
    Carbon::setTestNow();
});

function linkSharedSentinelHost(Server $alias, Server $owner): Server
{
    $alias->forceFill(['sentinel_owner_server_id' => $owner->id])->saveQuietly();
    Server::flushIdentityMap();

    return $alias->refresh();
}

it('keeps independent servers as independent Sentinel owners by default', function () {
    expect($this->owner->sentinelOwner()->is($this->owner))->toBeTrue()
        ->and($this->alias->sentinelOwner()->is($this->alias))->toBeTrue()
        ->and($this->owner->canManageSentinel())->toBeTrue()
        ->and($this->alias->canManageSentinel())->toBeTrue()
        ->and(StartSentinel::lockKey($this->owner))->not->toBe(StartSentinel::lockKey($this->alias));
});

it('does not infer shared ownership from matching physical endpoint names', function () {
    $this->alias->forceFill(['ip' => $this->owner->ip])->saveQuietly();

    expect($this->owner->canManageSentinel())->toBeTrue()
        ->and($this->alias->refresh()->canManageSentinel())->toBeTrue();
});

it('links Doctor VPS id 1 to localhost id 0 without changing the localhost record', function () {
    $localhostUpdatedAt = $this->owner->updated_at;
    $localhostSettingsUpdatedAt = $this->owner->settings->updated_at;

    $alias = linkSharedSentinelHost($this->alias, $this->owner);

    expect($this->owner->id)->toBe(0)
        ->and($alias->id)->toBe(1)
        ->and($alias->sentinel_owner_server_id)->toBe(0)
        ->and($alias->sentinelOwner()->is($this->owner))->toBeTrue()
        ->and($alias->canManageSentinel())->toBeFalse()
        ->and($this->owner->canManageSentinel())->toBeTrue()
        ->and(StartSentinel::lockKey($alias))->toBe(StartSentinel::lockKey($this->owner))
        ->and($this->owner->fresh()->sentinel_owner_server_id)->toBeNull()
        ->and($this->owner->fresh()->updated_at)->toEqual($localhostUpdatedAt)
        ->and($this->owner->settings->fresh()->updated_at)->toEqual($localhostSettingsUpdatedAt);
});

it('does not run Docker commands when an alias directly requests a Sentinel restart', function () {
    $alias = linkSharedSentinelHost($this->alias, $this->owner);
    Process::fake();
    Event::fake();

    StartSentinel::run($alias, restart: true, latestVersion: '1.0.2');

    Process::assertNothingRan();
});

it('does not remove the owners Sentinel when an alias requests a stop', function () {
    $alias = linkSharedSentinelHost($this->alias, $this->owner);
    $lastHeartbeat = $alias->sentinel_updated_at;
    Process::fake();

    StopSentinel::run($alias);

    Process::assertNothingRan();
    expect($alias->refresh()->sentinel_updated_at)->toEqual($lastHeartbeat);
});

it('rechecks ownership before stopping Sentinel through a stale server object', function () {
    $staleAlias = Server::query()->findOrFail($this->alias->id);
    $staleAlias->forceFill([
        'private_key_id' => \App\Models\PrivateKey::factory()->create(['team_id' => $staleAlias->team_id])->id,
    ])->saveQuietly();
    config(['constants.ssh.mux_enabled' => false]);
    DB::table('servers')->where('id', $staleAlias->id)->update([
        'sentinel_owner_server_id' => $this->owner->id,
    ]);
    Process::fake();

    StopSentinel::run($staleAlias);

    Process::assertDidntRun(fn ($process) => str_contains(
        is_array(remoteProcessScript($process)) ? implode(' ', remoteProcessScript($process)) : remoteProcessScript($process),
        'docker rm -f coolify-sentinel',
    ));
});

it('still stops Sentinel for a fresh independent owner', function () {
    $owner = $this->owner;
    $owner->forceFill([
        'private_key_id' => \App\Models\PrivateKey::factory()->create(['team_id' => $owner->team_id])->id,
    ])->saveQuietly();
    config(['constants.ssh.mux_enabled' => false]);
    Process::fake();

    StopSentinel::run($owner->refresh());

    Process::assertRan(function ($process) {
        $script = remoteProcessScript($process);

        return str_contains(is_array($script) ? implode(' ', $script) : $script, 'docker rm -f coolify-sentinel');
    });
});

it('does not delete the owners Sentinel when deleting an explicitly linked alias', function () {
    $alias = linkSharedSentinelHost($this->alias, $this->owner);
    $alias->forceFill([
        'private_key_id' => \App\Models\PrivateKey::factory()->create(['team_id' => $alias->team_id])->id,
    ])->saveQuietly();
    config(['constants.ssh.mux_enabled' => false]);
    expect($alias->refresh()->isFunctional())->toBeTrue()
        ->and((bool) $alias->settings->is_sentinel_enabled)->toBeTrue();

    Process::fake();
    \App\Actions\Server\DeleteServer::run($alias->id);

    Process::assertNothingRan();
    expect(Server::query()->find($this->owner->id))->not->toBeNull();
});

it('rechecks ownership changed after DeleteServer loads the server', function () {
    $alias = $this->alias;
    $alias->forceFill([
        'private_key_id' => \App\Models\PrivateKey::factory()->create(['team_id' => $alias->team_id])->id,
    ])->saveQuietly();
    config(['constants.ssh.mux_enabled' => false]);
    $ownershipChanged = false;
    $event = 'eloquent.retrieved: '.Server::class;
    Event::listen($event, function (Server $retrieved) use (&$ownershipChanged, $alias) {
        if ($ownershipChanged || $retrieved->id !== $alias->id) {
            return;
        }
        $ownershipChanged = true;
        DB::table('servers')->where('id', $alias->id)->update([
            'sentinel_owner_server_id' => $this->owner->id,
        ]);
    });
    Process::fake();

    try {
        DeleteServer::run($alias->id);
    } finally {
        Event::forget($event);
    }

    expect($ownershipChanged)->toBeTrue();
    Process::assertDidntRun(fn ($process) => str_contains(
        is_array(remoteProcessScript($process)) ? implode(' ', remoteProcessScript($process)) : remoteProcessScript($process),
        'docker rm -f coolify-sentinel',
    ));
    expect(Server::query()->find($this->owner->id))->not->toBeNull();
});

it('still stops Sentinel when deleting an independent server', function () {
    $owner = $this->owner;
    $owner->forceFill([
        'private_key_id' => \App\Models\PrivateKey::factory()->create(['team_id' => $owner->team_id])->id,
    ])->saveQuietly();
    config(['constants.ssh.mux_enabled' => false]);
    expect($owner->refresh()->isFunctional())->toBeTrue();

    Process::fake();
    \App\Actions\Server\DeleteServer::run($owner->id);

    Process::assertRan(function ($process) {
        $script = remoteProcessScript($process);

        return str_contains(is_array($script) ? implode(' ', $script) : $script, 'docker rm -f coolify-sentinel');
    });
});

it('does not inspect or restart Sentinel for an alias even after its restart cooldown expires', function () {
    $alias = linkSharedSentinelHost($this->alias, $this->owner);
    Cache::forget("sentinel:stall-restart:{$alias->id}");
    Http::fake();
    Process::fake();
    StartSentinel::shouldNotRun();

    (new CheckAndStartSentinelJob($alias))->handle();

    Process::assertNothingRan();
    expect(Cache::has("sentinel:stall-restart:{$alias->id}"))->toBeFalse();
});

it('rechecks ownership when a queued Sentinel check holds a stale server object', function () {
    $staleAlias = Server::query()->findOrFail($this->alias->id);
    $staleAlias->forceFill([
        'private_key_id' => \App\Models\PrivateKey::factory()->create(['team_id' => $staleAlias->team_id])->id,
    ])->saveQuietly();
    config(['constants.ssh.mux_enabled' => false]);
    $job = new CheckAndStartSentinelJob($staleAlias);
    DB::table('servers')->where('id', $staleAlias->id)->update([
        'sentinel_owner_server_id' => $this->owner->id,
    ]);
    Cache::put('coolify:versions', ['sentinel' => ['version' => '1.0.0']], 3600);
    Process::fake();
    StartSentinel::shouldNotRun();

    $job->handle();

    Process::assertDidntRun(fn ($process) => str_contains(
        is_array(remoteProcessScript($process)) ? implode(' ', remoteProcessScript($process)) : remoteProcessScript($process),
        'docker ps -a',
    ));
});

it('skips redundant connection and container checks on an alias while its owner is healthy', function () {
    $alias = linkSharedSentinelHost($this->alias, $this->owner);
    Carbon::setTestNow(now()->setMinute($alias->id % 60));
    $this->owner->forceFill(['sentinel_updated_at' => now()])->save();

    (new ServerManagerJob)->handle();

    Queue::assertNotPushed(ServerConnectionCheckJob::class, fn ($job) => $job->server->id === $alias->id);
    Queue::assertNotPushed(ServerCheckJob::class, fn ($job) => $job->server->id === $alias->id);
    Queue::assertNotPushed(CheckAndStartSentinelJob::class, fn ($job) => $job->server->id === $alias->id);
});

it('keeps independent SSH and container checks when an alias owns application resources', function () {
    $alias = linkSharedSentinelHost($this->alias, $this->owner);
    $project = Project::factory()->create(['team_id' => $alias->team_id]);
    $environment = Environment::factory()->create(['project_id' => $project->id]);
    $destination = StandaloneDocker::factory()->create([
        'server_id' => $alias->id,
        'name' => 'alias-app-destination',
        'network' => 'alias-app-network',
    ]);
    Application::factory()->create([
        'environment_id' => $environment->id,
        'destination_id' => $destination->id,
        'destination_type' => $destination->getMorphClass(),
    ]);
    $this->owner->forceFill(['sentinel_updated_at' => now()])->save();

    (new ServerManagerJob)->handle();

    Queue::assertPushed(ServerConnectionCheckJob::class, fn ($job) => $job->server->id === $alias->id);
    Queue::assertPushed(ServerCheckJob::class, fn ($job) => $job->server->id === $alias->id);
});

it('keeps SSH and container fallback checks when the shared owner loses heartbeat', function () {
    $alias = linkSharedSentinelHost($this->alias, $this->owner);
    $this->owner->forceFill(['sentinel_updated_at' => now()->subMinutes(20)])->save();

    (new ServerManagerJob)->handle();

    Queue::assertPushed(ServerConnectionCheckJob::class, fn ($job) => $job->server->id === $alias->id);
    Queue::assertPushed(ServerCheckJob::class, fn ($job) => $job->server->id === $alias->id);
});

it('continues the alias SSH fallback when the owner heartbeat is healthy but the alias connection is unavailable', function () {
    $alias = linkSharedSentinelHost($this->alias, $this->owner);
    $alias->settings->forceFill(['is_reachable' => false])->saveQuietly();
    $this->owner->forceFill(['sentinel_updated_at' => now()])->saveQuietly();

    (new ServerManagerJob)->handle();

    expect($this->owner->settings->fresh()->is_reachable)->toBeTrue()
        ->and($this->owner->isSentinelLive())->toBeTrue();
    Queue::assertPushed(ServerConnectionCheckJob::class, fn ($job) => $job->server->id === $alias->id);
});

it('fails closed for an invalid owner link without suppressing SSH recovery', function () {
    $this->alias->forceFill(['sentinel_owner_server_id' => 99999999])->saveQuietly();
    $alias = $this->alias->refresh();
    Process::fake();

    expect($alias->sentinelOwner())->toBeNull()
        ->and($alias->canManageSentinel())->toBeFalse();

    StartSentinel::run($alias, restart: true, latestVersion: '1.0.2');
    StopSentinel::run($alias);

    Process::assertNothingRan();

    (new ServerManagerJob)->handle();

    Queue::assertPushed(ServerConnectionCheckJob::class, fn ($job) => $job->server->id === $alias->id);
});

it('fails closed for owner cycles without attempting to start or stop either container', function () {
    $alias = linkSharedSentinelHost($this->alias, $this->owner);
    $this->owner->forceFill(['sentinel_owner_server_id' => $alias->id])->saveQuietly();
    $owner = $this->owner->refresh();
    Process::fake();

    expect($owner->sentinelOwner())->toBeNull()
        ->and($alias->refresh()->sentinelOwner())->toBeNull();

    StartSentinel::run($owner, restart: true, latestVersion: '1.0.2');
    StopSentinel::run($alias);

    Process::assertNothingRan();
});
