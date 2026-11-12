<?php

namespace App\Actions\Server;

use App\Models\Server;
use Illuminate\Support\Facades\Cache;
use Lorisleiva\Actions\Concerns\AsAction;

class StopSentinel
{
    use AsAction;

    public function handle(Server $server, bool $lockAcquired = false, ?int $timeout = null, ?int $maxAttempts = null): void
    {
        if (! $server->canManageSentinel()) {
            return;
        }

        if ($lockAcquired) {
            $this->stop($server, $timeout, $maxAttempts);

            return;
        }

        Cache::lock(StartSentinel::lockKey($server), StartSentinel::LOCK_SECONDS)->block(
            StartSentinel::LOCK_WAIT_SECONDS,
            fn () => $this->stop($server, $timeout, $maxAttempts),
        );
    }

    private function stop(Server $server, ?int $timeout, ?int $maxAttempts): void
    {
        // Ownership may change while this action waits for another Sentinel operation.
        $server->refresh();
        if (! $server->canManageSentinel()) {
            return;
        }

        instant_remote_process(
            ['docker rm -f coolify-sentinel'],
            $server,
            false,
            timeout: $timeout,
            maxAttempts: $maxAttempts,
        );
        $server->sentinelHeartbeat(isReset: true);
    }
}
