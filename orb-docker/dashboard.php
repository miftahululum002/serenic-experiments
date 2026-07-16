<?php
session_start();
if (!isset($_SESSION['user'])) {
    header('Location: index.php');
    exit;
}
$service_name = $_ENV['SERVICE_NAME'] ?? '-';
$container_id = gethostname();
?>
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard</title>
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <div class="container">
        <h2>Dashboard</h2>
        <p>Selamat datang, <strong><?= htmlspecialchars($_SESSION['user']) ?></strong>!</p>
        <p>Service: <strong><?= htmlspecialchars($service_name) ?></strong></p>
        <p>Container: <strong><?= htmlspecialchars($container_id) ?></strong></p>
        <a href="logout.php" class="btn-logout">Logout</a>
    </div>
</body>
</html>
