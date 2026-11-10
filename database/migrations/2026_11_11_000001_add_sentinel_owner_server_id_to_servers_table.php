<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::table('servers', function (Blueprint $table) {
            // Null means this logical server owns an independent Sentinel.
            // An explicit owner is required because IPs, DNS names and SSH endpoints
            // alone do not prove that two server records share a Docker daemon.
            $table->unsignedBigInteger('sentinel_owner_server_id')->nullable()->index();
        });
    }

    public function down(): void
    {
        Schema::table('servers', function (Blueprint $table) {
            $table->dropIndex(['sentinel_owner_server_id']);
            $table->dropColumn('sentinel_owner_server_id');
        });
    }
};
