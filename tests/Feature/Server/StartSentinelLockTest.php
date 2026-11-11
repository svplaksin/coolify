<?php

use App\Actions\Server\StartSentinel;
use App\Actions\Server\StopSentinel;
use App\Events\SentinelRestarted;
use App\Livewire\Server\Charts;
use App\Livewire\Server\Sentinel;
use App\Models\InstanceSettings;
use App\Models\PrivateKey;
use App\Models\Server;
use App\Models\User;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Event;
use Illuminate\Support\Facades\Process;
use Illuminate\Support\Facades\Queue;
use Livewire\Livewire;
use Lorisleiva\Actions\Decorators\JobDecorator;

uses(RefreshDatabase::class);

beforeEach(function () {
    Server::flushIdentityMap();
    Queue::fake();
    Event::fake([SentinelRestarted::class]);
    InstanceSettings::forceCreate(['id' => 0]);
    config(['constants.ssh.mux_enabled' => false]);

    $this->user = User::factory()->create();
    $this->team = $this->user->teams()->first();
    $this->actingAs($this->user);
    session(['currentTeam' => $this->team]);

    $this->server = Server::factory()->create([
        'team_id' => $this->team->id,
        'private_key_id' => PrivateKey::factory()->create(['team_id' => $this->team->id])->id,
    ]);
    $this->server->settings->forceFill([
        'is_sentinel_enabled' => true,
        'sentinel_custom_url' => 'https://coolify.example.com',
    ])->saveQuietly();
});

function queuedSentinelStarts(): int
{
    return Queue::pushed(JobDecorator::class, fn (JobDecorator $job): bool => $job->getAction() instanceof StartSentinel)->count();
}

it('holds the per-server lock while it replaces the Sentinel container', function () {
    $lockHeldDuringDockerRun = null;
    Process::fake(function ($process) use (&$lockHeldDuringDockerRun) {
        $script = is_array(remoteProcessScript($process)) ? implode(' ', remoteProcessScript($process)) : remoteProcessScript($process);
        if (str_contains($script, 'docker run -d')) {
            $lockHeldDuringDockerRun = ! Cache::lock(StartSentinel::lockKey($this->server), 1)->get();
        }

        return Process::result(output: '');
    });

    StartSentinel::run($this->server, restart: true, latestVersion: '1.0.2');

    expect($lockHeldDuringDockerRun)->toBeTrue()
        ->and(Cache::lock(StartSentinel::lockKey($this->server), 1)->get())->toBeTrue();
});

it('starts Sentinel with the settings saved after the server was loaded', function () {
    $staleServer = Server::query()->findOrFail($this->server->id);
    $staleServer->settings;
    Server::query()->findOrFail($this->server->id)->settings->forceFill(['sentinel_push_interval_seconds' => 33])->saveQuietly();

    $scripts = [];
    Process::fake(function ($process) use (&$scripts) {
        $scripts[] = is_array(remoteProcessScript($process)) ? implode(' ', remoteProcessScript($process)) : remoteProcessScript($process);

        return Process::result(output: '');
    });

    StartSentinel::run($staleServer, restart: true, latestVersion: '1.0.2');

    $script = collect($scripts)->first(fn (string $command): bool => str_contains($command, 'docker run -d'));
    expect($script)->not->toBeNull();

    // Since v4.7.5, Sentinel settings are passed in a temporary base64-encoded
    // env file rather than exposed as plaintext arguments to docker run.
    preg_match_all('/[A-Za-z0-9+\\/]{80,}={0,2}/', $script, $matches);
    $environment = collect($matches[0])
        ->map(fn (string $encoded) => base64_decode($encoded, true))
        ->first(fn ($decoded) => is_string($decoded) && str_contains($decoded, 'PUSH_INTERVAL_SECONDS='));

    expect($environment)->toContain('PUSH_INTERVAL_SECONDS=33');
});

it('rechecks ownership after acquiring the Sentinel start lock', function () {
    $owner = Server::factory()->create([
        'team_id' => $this->team->id,
        'private_key_id' => $this->server->private_key_id,
    ]);
    $staleAlias = Server::query()->findOrFail($this->server->id);
    $lockKey = StartSentinel::lockKey($staleAlias);
    $lock = Mockery::mock(\Illuminate\Contracts\Cache\Lock::class);
    $lock->shouldReceive('block')
        ->once()
        ->with(StartSentinel::LOCK_WAIT_SECONDS, Mockery::type(Closure::class))
        ->andReturnUsing(function (int $seconds, Closure $callback) use ($owner, $staleAlias) {
            DB::table('servers')->where('id', $staleAlias->id)->update([
                'sentinel_owner_server_id' => $owner->id,
            ]);

            return $callback();
        });
    Cache::shouldReceive('lock')
        ->once()
        ->with($lockKey, StartSentinel::LOCK_SECONDS)
        ->andReturn($lock);
    Process::fake();

    StartSentinel::run($staleAlias, restart: true, latestVersion: '1.0.2');

    Process::assertNothingRan();
});

it('rechecks ownership after acquiring the Sentinel stop lock', function () {
    $owner = Server::factory()->create([
        'team_id' => $this->team->id,
        'private_key_id' => $this->server->private_key_id,
    ]);
    $staleAlias = Server::query()->findOrFail($this->server->id);
    $lockKey = StartSentinel::lockKey($staleAlias);
    $lock = Mockery::mock(\Illuminate\Contracts\Cache\Lock::class);
    $lock->shouldReceive('block')
        ->once()
        ->with(StartSentinel::LOCK_WAIT_SECONDS, Mockery::type(Closure::class))
        ->andReturnUsing(function (int $seconds, Closure $callback) use ($owner, $staleAlias) {
            DB::table('servers')->where('id', $staleAlias->id)->update([
                'sentinel_owner_server_id' => $owner->id,
            ]);

            return $callback();
        });
    Cache::shouldReceive('lock')
        ->once()
        ->with($lockKey, StartSentinel::LOCK_SECONDS)
        ->andReturn($lock);
    Process::fake();

    StopSentinel::run($staleAlias);

    Process::assertNothingRan();
});

it('starts Sentinel once when the metrics page toggles metrics', function () {
    StartSentinel::shouldRun()->once();

    Livewire::test(Charts::class, ['server_uuid' => $this->server->uuid])
        ->call('toggleMetrics');

    expect(queuedSentinelStarts())->toBe(0);
});

it('restarts Sentinel once when the Sentinel page changes debug logging', function () {
    Livewire::test(Sentinel::class, ['server' => $this->server])
        ->set('isSentinelDebugEnabled', true)
        ->call('instantSave');

    expect(queuedSentinelStarts())->toBe(1);
});

