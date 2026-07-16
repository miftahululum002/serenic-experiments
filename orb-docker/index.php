<?php
session_start();
if (isset($_SESSION['user'])) {
    header('Location: dashboard.php');
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
    <title>Login</title>
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <div class="container">
        <h2>Login</h2>
        <form action="login.php" method="POST">
            <label>Username</label>
            <input type="text" name="username" required>
            <label>Password</label>
            <input type="password" name="password" required>
            <button type="submit">Masuk</button>
            <?php if (isset($_GET['error'])): ?>
                <p class="error">Username atau password salah!</p>
            <?php endif; ?>
        </form>
        <p class="server-info">Service: <?= htmlspecialchars($service_name) ?> | Container: <?= htmlspecialchars($container_id) ?></p>
    </div>
</body>
</html>
