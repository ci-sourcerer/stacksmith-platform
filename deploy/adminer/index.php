<?php
// Adapted from common-python-tasks, Copyright (c) 2026 Joseph Asbury (MIT).
if (!count($_GET)) {
    $_POST['auth'] = [
        'server' => $_ENV['ADMINER_DEFAULT_SERVER'],
        'username' => $_ENV['ADMINER_DEFAULT_USERNAME'],
        'password' => $_ENV['ADMINER_DEFAULT_PASSWORD'],
        'driver' => 'pgsql',
        'db' => $_ENV['ADMINER_DEFAULT_DB'],
    ];
}
include './adminer.php';
