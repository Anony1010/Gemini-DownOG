import os
import time
from flask import Flask, jsonify, request, Response
from database import (
    db_get_all_users, db_ban_user, db_count_users, db_count_banned_users,
    db_get_all_clones, db_add_clone, db_delete_clone, get_setting, set_setting
)
from shared import logger, active_clones, download_queue, LOG_FILE

try:
    import psutil
except ImportError:
    psutil = None

app = Flask(__name__)

# Helper to get system statistics
def get_system_stats():
    stats = {
        "cpu": 0,
        "ram": 0,
        "disk": 0,
        "active_downloads": download_queue.qsize(),
        "active_clones": len(active_clones),
        "total_users": db_count_users(),
        "banned_users": db_count_banned_users()
    }
    
    if psutil:
        try:
            stats["cpu"] = psutil.cpu_percent(interval=None)
            stats["ram"] = psutil.virtual_memory().percent
            stats["disk"] = psutil.disk_usage('/').percent
        except Exception as e:
            logger.error(f"Error reading system stats: {e}")
    else:
        # Fallback using os module or static values
        try:
            load = os.getloadavg()
            stats["cpu"] = round((load[0] / os.cpu_count()) * 100, 1)
        except Exception:
            stats["cpu"] = 12.5
        stats["ram"] = 42.0
        stats["disk"] = 62.0
        
    return stats

@app.route("/api/stats", methods=["GET"])
def api_stats():
    """Returns JSON of system and bot statistics."""
    return jsonify(get_system_stats())

@app.route("/api/users", methods=["GET"])
def api_users():
    """Returns JSON of all registered users."""
    try:
        users = db_get_all_users()
        return jsonify({"users": users})
    except Exception as e:
        logger.error(f"Failed to fetch users: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/users/ban", methods=["POST"])
def api_users_ban():
    """Bans or unbans a user."""
    data = request.get_json() or {}
    user_id = data.get("user_id")
    is_banned = data.get("is_banned")
    if user_id is None or is_banned is None:
        return jsonify({"error": "Invalid parameters"}), 400
    try:
        db_ban_user(user_id, int(is_banned))
        action = "banned" if is_banned else "unbanned"
        logger.info(f"User {user_id} has been {action} via dashboard.")
        return jsonify({"status": "success", "message": f"User {user_id} {action}"})
    except Exception as e:
        logger.error(f"Failed to ban/unban user {user_id}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/clones", methods=["GET"])
def api_clones():
    """Gets list of all cloned bots."""
    clones = db_get_all_clones()
    for c in clones:
        c["status"] = "Aktiv" if c["token"] in active_clones else "Qeyri-aktiv"
    return jsonify({"clones": clones})

@app.route("/api/clones/add", methods=["POST"])
def api_clones_add():
    """Registers a cloned bot (starts it in bot runner)."""
    data = request.get_json() or {}
    token = data.get("token")
    if not token:
        return jsonify({"error": "Token required"}), 400
        
    from bot import start_bot_instance
    success, res = start_bot_instance(token)
    if success:
        db_add_clone(token, res)
        logger.info(f"Clone bot @{res} added and started via dashboard.")
        return jsonify({"status": "success", "username": res})
    else:
        logger.error(f"Failed to add clone bot: {res}")
        return jsonify({"error": f"Bot starting failed: {res}"}), 400

@app.route("/api/clones/delete", methods=["DELETE"])
def api_clones_delete():
    """Stops and deletes a cloned bot."""
    data = request.get_json() or {}
    token = data.get("token")
    if not token:
        return jsonify({"error": "Token required"}), 400
    try:
        db_delete_clone(token)
        if token in active_clones:
            try:
                active_clones[token].stop_polling()
                del active_clones[token]
            except Exception as e:
                logger.error(f"Error stopping clone bot: {e}")
        logger.info("Clone bot deleted via dashboard.")
        return jsonify({"status": "success", "message": "Klon bot silindi"})
    except Exception as e:
        logger.error(f"Failed to delete clone: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    """Gets current branding and start messages."""
    return jsonify({
        "branding": get_setting("caption_branding") or "⚓ BY ORUJOV ⚓",
        "start_message": get_setting("start_message") or (
            "👋 **Salam! Mən media yükləyici botam.**\n\n"
            "Linkləri mənə göndərin və mən onları yükləyib sizə göndərim:\n"
            "🎵 **YouTube** linki göndərdikdə -> **MP3** (səs)\n"
            "🎬 **Instagram** və ya **TikTok** linki göndərdikdə -> **MP4** (video)\n\n"
            "Sadəcə linki kopyalayıb bura yapışdırın!"
        )
    })

@app.route("/api/settings/save", methods=["POST"])
def api_settings_save():
    """Saves branding and start messages."""
    data = request.get_json() or {}
    branding = data.get("branding")
    start_message = data.get("start_message")
    
    if branding is None or start_message is None:
        return jsonify({"error": "Parameters missing"}), 400
    try:
        set_setting("caption_branding", branding)
        set_setting("start_message", start_message)
        logger.info("Settings updated via dashboard.")
        return jsonify({"status": "success", "message": "Tənzimləmələr yadda saxlanıldı"})
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/logs", methods=["GET"])
def api_logs():
    """Reads the last 100 lines of the system logs."""
    if not os.path.exists(LOG_FILE):
        return jsonify({"logs": "Log faylı tapılmadı."})
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            last_lines = lines[-100:]
            return jsonify({"logs": "".join(last_lines)})
    except Exception as e:
        return jsonify({"logs": f"Logları oxumaq mümkün olmadı: {e}"})

@app.route("/metrics", methods=["GET"])
def api_metrics():
    """Prometheus monitoring metrics."""
    stats = get_system_stats()
    metrics = [
        f"system_cpu_usage {stats['cpu']}",
        f"system_ram_usage {stats['ram']}",
        f"system_disk_usage {stats['disk']}",
        f"bot_active_downloads {stats['active_downloads']}",
        f"bot_active_clones {stats['active_clones']}",
        f"bot_total_users {stats['total_users']}",
        f"bot_banned_users {stats['banned_users']}"
    ]
    return Response("\n".join(metrics), mimetype="text/plain")

@app.route("/", methods=["GET"])
def index():
    """Serves the dashboard single page application."""
    html_content = """
    <!DOCTYPE html>
    <html lang="az">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Gemini-DownOG Dashboard</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&display=swap" rel="stylesheet">
        <style>
            :root {
                --bg-gradient: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%);
                --card-bg: rgba(30, 41, 59, 0.45);
                --card-border: rgba(255, 255, 255, 0.08);
                --text-main: #f8fafc;
                --text-muted: #94a3b8;
                --accent: #6366f1;
                --accent-hover: #4f46e5;
                --success: #10b981;
                --danger: #ef4444;
                --glow: 0 0 15px rgba(99, 102, 241, 0.4);
            }

            * {
                box-sizing: border-box;
                margin: 0;
                padding: 0;
                font-family: 'Outfit', sans-serif;
            }

            body {
                background: var(--bg-gradient);
                color: var(--text-main);
                min-height: 100vh;
                display: flex;
                overflow-x: hidden;
            }

            /* Sidebar */
            aside {
                width: 260px;
                background: rgba(15, 23, 42, 0.8);
                border-right: 1px solid var(--card-border);
                backdrop-filter: blur(12px);
                display: flex;
                flex-direction: column;
                padding: 2rem 1.5rem;
                position: fixed;
                height: 100vh;
            }

            .logo {
                font-size: 1.5rem;
                font-weight: 700;
                background: linear-gradient(to right, #818cf8, #c084fc);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                margin-bottom: 3rem;
                display: flex;
                align-items: center;
                gap: 0.5rem;
            }

            .nav-list {
                list-style: none;
                display: flex;
                flex-direction: column;
                gap: 0.5rem;
            }

            .nav-item {
                padding: 0.85rem 1rem;
                border-radius: 8px;
                cursor: pointer;
                color: var(--text-muted);
                font-weight: 600;
                transition: all 0.3s ease;
                display: flex;
                align-items: center;
                gap: 0.75rem;
            }

            .nav-item:hover, .nav-item.active {
                background: rgba(99, 102, 241, 0.15);
                color: var(--text-main);
                border-left: 3px solid var(--accent);
            }

            /* Main Content */
            main {
                margin-left: 260px;
                flex-grow: 1;
                padding: 2.5rem;
                max-width: 1200px;
                width: calc(100% - 260px);
            }

            header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 2.5rem;
            }

            h1 {
                font-size: 2rem;
                font-weight: 700;
            }

            .status-badge {
                padding: 0.5rem 1rem;
                border-radius: 20px;
                background: rgba(16, 185, 129, 0.1);
                color: var(--success);
                font-weight: 600;
                border: 1px solid rgba(16, 185, 129, 0.2);
                display: flex;
                align-items: center;
                gap: 0.5rem;
            }

            /* Dashboard Cards Grid */
            .grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
                gap: 1.5rem;
                margin-bottom: 2.5rem;
            }

            .card {
                background: var(--card-bg);
                border: 1px solid var(--card-border);
                border-radius: 16px;
                padding: 1.5rem;
                backdrop-filter: blur(12px);
                box-shadow: 0 4px 30px rgba(0, 0, 0, 0.1);
                transition: transform 0.3s ease, border-color 0.3s ease;
            }

            .card:hover {
                transform: translateY(-5px);
                border-color: rgba(99, 102, 241, 0.3);
            }

            .card-title {
                font-size: 0.875rem;
                color: var(--text-muted);
                font-weight: 600;
                text-transform: uppercase;
                margin-bottom: 0.5rem;
            }

            .card-value {
                font-size: 1.8rem;
                font-weight: 700;
                margin-bottom: 0.5rem;
            }

            .progress-bar {
                width: 100%;
                height: 6px;
                background: rgba(255, 255, 255, 0.1);
                border-radius: 3px;
                overflow: hidden;
            }

            .progress-fill {
                height: 100%;
                background: var(--accent);
                width: 0%;
                transition: width 0.8s ease;
            }

            /* Section Views */
            .section {
                display: none;
            }

            .section.active {
                display: block;
                animation: fadeIn 0.4s ease;
            }

            @keyframes fadeIn {
                from { opacity: 0; transform: translateY(10px); }
                to { opacity: 1; transform: translateY(0); }
            }

            /* Forms & Tables */
            .content-block {
                background: var(--card-bg);
                border: 1px solid var(--card-border);
                border-radius: 16px;
                padding: 2rem;
                backdrop-filter: blur(12px);
                margin-bottom: 2rem;
            }

            .block-title {
                font-size: 1.25rem;
                font-weight: 700;
                margin-bottom: 1.5rem;
                border-bottom: 1px solid var(--card-border);
                padding-bottom: 0.75rem;
            }

            .form-group {
                margin-bottom: 1.25rem;
            }

            label {
                display: block;
                font-size: 0.875rem;
                color: var(--text-muted);
                margin-bottom: 0.5rem;
                font-weight: 600;
            }

            input[type="text"], textarea {
                width: 100%;
                padding: 0.75rem 1rem;
                border-radius: 8px;
                border: 1px solid var(--card-border);
                background: rgba(15, 23, 42, 0.6);
                color: var(--text-main);
                font-size: 0.95rem;
                outline: none;
                transition: border-color 0.3s;
            }

            input[type="text"]:focus, textarea:focus {
                border-color: var(--accent);
                box-shadow: var(--glow);
            }

            button {
                padding: 0.75rem 1.5rem;
                border-radius: 8px;
                border: none;
                background: var(--accent);
                color: white;
                font-weight: 600;
                cursor: pointer;
                transition: background 0.3s, transform 0.1s;
            }

            button:hover {
                background: var(--accent-hover);
            }

            button:active {
                transform: scale(0.98);
            }

            .btn-danger {
                background: var(--danger);
            }
            .btn-danger:hover {
                background: #dc2626;
            }

            /* Tables */
            table {
                width: 100%;
                border-collapse: collapse;
                margin-top: 1rem;
            }

            th, td {
                padding: 1rem;
                text-align: left;
                border-bottom: 1px solid var(--card-border);
            }

            th {
                color: var(--text-muted);
                font-weight: 600;
            }

            tr:hover td {
                background: rgba(255, 255, 255, 0.02);
            }

            /* Log Viewer styling */
            pre.log-console {
                background: #090d16;
                padding: 1rem;
                border-radius: 12px;
                max-height: 400px;
                overflow-y: auto;
                font-family: 'Courier New', Courier, monospace;
                font-size: 0.85rem;
                color: #38bdf8;
                line-height: 1.4;
                border: 1px solid var(--card-border);
            }
        </style>
    </head>
    <body>
        <aside>
            <div class="logo">🚀 Gemini-DownOG</div>
            <ul class="nav-list">
                <li class="nav-item active" onclick="switchSection('dashboard')">📊 Dashboard</li>
                <li class="nav-item" onclick="switchSection('users')">👥 İstifadəçilər</li>
                <li class="nav-item" onclick="switchSection('clones')">🤖 Klon Botlar</li>
                <li class="nav-item" onclick="switchSection('settings')">⚙️ Tənzimləmələr</li>
                <li class="nav-item" onclick="switchSection('logs')">📝 Sistem Logları</li>
            </ul>
        </aside>

        <main>
            <header>
                <h1 id="page-title">İnformasiya Paneli</h1>
                <div class="status-badge"><span style="width: 8px; height: 8px; border-radius: 50%; background: var(--success); display: inline-block;"></span> Bot Online</div>
            </header>

            <!-- Dashboard Section -->
            <div id="section-dashboard" class="section active">
                <div class="grid">
                    <div class="card">
                        <div class="card-title">CPU Yüklənməsi</div>
                        <div class="card-value" id="stat-cpu">0%</div>
                        <div class="progress-bar"><div class="progress-fill" id="fill-cpu" style="width: 0%;"></div></div>
                    </div>
                    <div class="card">
                        <div class="card-title">RAM İstifadəsi</div>
                        <div class="card-value" id="stat-ram">0%</div>
                        <div class="progress-bar"><div class="progress-fill" id="fill-ram" style="width: 0%;"></div></div>
                    </div>
                    <div class="card">
                        <div class="card-title">Disk İstifadəsi</div>
                        <div class="card-value" id="stat-disk">0%</div>
                        <div class="progress-bar"><div class="progress-fill" id="fill-disk" style="width: 0%;"></div></div>
                    </div>
                    <div class="card">
                        <div class="card-title">Aktiv Klonlar</div>
                        <div class="card-value" id="stat-clones">0</div>
                    </div>
                    <div class="card">
                        <div class="card-title">Növbədəki yükləmələr</div>
                        <div class="card-value" id="stat-downloads">0</div>
                    </div>
                </div>

                <div class="content-block">
                    <div class="block-title">📊 Ümumi Statistika</div>
                    <div style="display: flex; gap: 2rem;">
                        <div>
                            <div class="card-title">Ümumi İstifadəçi Siyahısı</div>
                            <div class="card-value" id="total-users">0</div>
                        </div>
                        <div>
                            <div class="card-title">Bloklanmış İstifadəçilər</div>
                            <div class="card-value" style="color: var(--danger);" id="banned-users">0</div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Users Section -->
            <div id="section-users" class="section">
                <div class="content-block">
                    <div class="block-title">👥 Bütün İstifadəçilər</div>
                    <table>
                        <thead>
                            <tr>
                                <th>İstifadəçi ID</th>
                                <th>Ad</th>
                                <th>İstifadəçi Adı</th>
                                <th>Status</th>
                                <th>Qoşulma Tarixi</th>
                                <th>Əməliyyat</th>
                            </tr>
                        </thead>
                        <tbody id="users-table-body">
                            <!-- Populated dynamically -->
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Clones Section -->
            <div id="section-clones" class="section">
                <div class="content-block">
                    <div class="block-title">➕ Yeni Bot Klonla</div>
                    <div class="form-group">
                        <label>Bot Tokeni</label>
                        <input type="text" id="clone-token" placeholder="BotFather-dən aldığınız tokeni yazın...">
                    </div>
                    <button onclick="addClone()">🤖 Klonu Başlat</button>
                </div>

                <div class="content-block">
                    <div class="block-title">🤖 Aktiv Klon Botlar</div>
                    <table>
                        <thead>
                            <tr>
                                <th>Username</th>
                                <th>Token</th>
                                <th>Qeydiyyat Tarixi</th>
                                <th>Status</th>
                                <th>Əməliyyat</th>
                            </tr>
                        </thead>
                        <tbody id="clones-table-body">
                            <!-- Populated dynamically -->
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Settings Section -->
            <div id="section-settings" class="section">
                <div class="content-block">
                    <div class="block-title">⚙️ Brendinq və Başlanğıc Tənzimləmələri</div>
                    <div class="form-group">
                        <label>Yüklənən Media Altındakı Mətn (Branding Caption)</label>
                        <input type="text" id="branding-caption" placeholder="Məs: ⚓ BY ORUJOV ⚓">
                    </div>
                    <div class="form-group">
                        <label>/start Mesajı (Xoş gəldiniz mətni)</label>
                        <textarea id="start-msg-text" rows="8" placeholder="Botu başladanda gələn mətn..."></textarea>
                    </div>
                    <button onclick="saveSettings()">💾 Yadda saxla</button>
                </div>
            </div>

            <!-- Logs Section -->
            <div id="section-logs" class="section">
                <div class="content-block">
                    <div class="block-title">📝 Sistem Logları (Son 100 sətir)</div>
                    <pre class="log-console" id="logs-container">Loglar yüklənir...</pre>
                    <button style="margin-top: 1rem;" onclick="loadLogs()">🔄 Yenilə</button>
                </div>
            </div>
        </main>

        <script>
            function switchSection(sectionId) {
                // Remove active classes
                document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
                document.querySelectorAll('.section').forEach(el => el.classList.remove('active'));
                
                // Add active to current
                const clickedNav = Array.from(document.querySelectorAll('.nav-item')).find(el => el.innerText.toLowerCase().includes(sectionId === 'dashboard' ? 'dashboard' : sectionId === 'users' ? 'istifadəçilər' : sectionId === 'clones' ? 'klon' : sectionId === 'settings' ? 'tənzimləmələr' : 'log'));
                if (clickedNav) clickedNav.classList.add('active');
                
                document.getElementById(`section-${sectionId}`).classList.add('active');
                
                // Set Header title
                const titles = {
                    dashboard: "İnformasiya Paneli",
                    users: "İstifadəçilərin İdarə Edilməsi",
                    clones: "Bot Klonlama Mərkəzi",
                    settings: "Brendinq Tənzimləmələri",
                    logs: "Sistem Log Girişləri"
                };
                document.getElementById('page-title').innerText = titles[sectionId];
                
                // Specific actions
                if (sectionId === 'dashboard') loadStats();
                else if (sectionId === 'users') loadUsers();
                else if (sectionId === 'clones') loadClones();
                else if (sectionId === 'settings') loadSettings();
                else if (sectionId === 'logs') loadLogs();
            }

            async function loadStats() {
                try {
                    const res = await fetch('/api/stats');
                    const data = await res.json();
                    
                    document.getElementById('stat-cpu').innerText = `${data.cpu}%`;
                    document.getElementById('fill-cpu').style.width = `${data.cpu}%`;
                    
                    document.getElementById('stat-ram').innerText = `${data.ram}%`;
                    document.getElementById('fill-ram').style.width = `${data.ram}%`;
                    
                    document.getElementById('stat-disk').innerText = `${data.disk}%`;
                    document.getElementById('fill-disk').style.width = `${data.disk}%`;
                    
                    document.getElementById('stat-clones').innerText = data.active_clones;
                    document.getElementById('stat-downloads').innerText = data.active_downloads;
                    
                    document.getElementById('total-users').innerText = data.total_users;
                    document.getElementById('banned-users').innerText = data.banned_users;
                } catch (e) {
                    console.error("Stats fetch failed", e);
                }
            }

            async function loadUsers() {
                try {
                    const res = await fetch('/api/users');
                    const data = await res.json();
                    const body = document.getElementById('users-table-body');
                    body.innerHTML = '';
                    
                    data.users.forEach(u => {
                        const tr = document.createElement('tr');
                        const status = u.is_banned ? '<span style="color: var(--danger); font-weight:600;">Bloklanıb</span>' : '<span style="color: var(--success); font-weight:600;">Aktiv</span>';
                        const actionBtn = u.is_banned 
                            ? `<button style="padding: 0.4rem 0.8rem;" onclick="toggleBan(${u.user_id}, 0)">🔓 Aç</button>`
                            : `<button class="btn-danger" style="padding: 0.4rem 0.8rem;" onclick="toggleBan(${u.user_id}, 1)">🚫 Blokla</button>`;
                        
                        tr.innerHTML = `
                            <td><code>${u.user_id}</code></td>
                            <td>${u.first_name || 'İstifadəçi'}</td>
                            <td>${u.username ? '@' + u.username : 'Yoxdur'}</td>
                            <td>${status}</td>
                            <td>${u.joined_at.replace('T', ' ').substring(0, 16)}</td>
                            <td>${actionBtn}</td>
                        `;
                        body.appendChild(tr);
                    });
                } catch (e) {
                    alert("İstifadəçilər yüklənərkən xəta baş verdi");
                }
            }

            async function toggleBan(userId, isBanned) {
                try {
                    const res = await fetch('/api/users/ban', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({user_id: userId, is_banned: isBanned})
                    });
                    const r = await res.json();
                    if (r.status === 'success') {
                        loadUsers();
                    }
                } catch (e) {
                    alert("Əməliyyat uğursuz oldu");
                }
            }

            async function loadClones() {
                try {
                    const res = await fetch('/api/clones');
                    const data = await res.json();
                    const body = document.getElementById('clones-table-body');
                    body.innerHTML = '';
                    
                    data.clones.forEach(c => {
                        const tr = document.createElement('tr');
                        const statusColor = c.status === 'Aktiv' ? 'var(--success)' : 'var(--danger)';
                        
                        tr.innerHTML = `
                            <td><b>@${c.username}</b></td>
                            <td><code>${c.token.substring(0, 12)}...</code></td>
                            <td>${c.created_at.substring(0, 16)}</td>
                            <td><span style="color: ${statusColor}; font-weight:600;">${c.status}</span></td>
                            <td><button class="btn-danger" style="padding: 0.4rem 0.8rem;" onclick="deleteClone('${c.token}')">❌ Sil</button></td>
                        `;
                        body.appendChild(tr);
                    });
                } catch (e) {
                    alert("Klon botlar yüklənərkən xəta baş verdi");
                }
            }

            async function addClone() {
                const token = document.getElementById('clone-token').value.trim();
                if (!token) {
                    alert("Zəhmət olmasa token daxil edin");
                    return;
                }
                
                try {
                    const res = await fetch('/api/clones/add', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({token})
                    });
                    const r = await res.json();
                    if (res.ok) {
                        alert(`Klon bot @${r.username} aktivləşdirildi!`);
                        document.getElementById('clone-token').value = '';
                        loadClones();
                    } else {
                        alert(`Xəta: ${r.error || r.detail}`);
                    }
                } catch (e) {
                    alert("Klon əlavə edilə bilmədi");
                }
            }

            async function deleteClone(token) {
                if (!confirm("Bu klon botu silmək istədiyinizə əminsiniz?")) return;
                
                try {
                    const res = await fetch('/api/clones/delete', {
                        method: 'DELETE',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({token})
                    });
                    if (res.ok) {
                        loadClones();
                    }
                } catch (e) {
                    alert("Klon silinə bilmədi");
                }
            }

            async function loadSettings() {
                try {
                    const res = await fetch('/api/settings');
                    const data = await res.json();
                    document.getElementById('branding-caption').value = data.branding;
                    document.getElementById('start-msg-text').value = data.start_message;
                } catch (e) {
                    alert("Tənzimləmələr yüklənərkən xəta baş verdi");
                }
            }

            async function saveSettings() {
                const branding = document.getElementById('branding-caption').value.trim();
                const start_message = document.getElementById('start-msg-text').value.trim();
                
                try {
                    const res = await fetch('/api/settings/save', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({branding, start_message})
                    });
                    if (res.ok) {
                        alert("Tənzimləmələr uğurla yadda saxlanıldı!");
                    } else {
                        alert("Xəta baş verdi");
                    }
                } catch (e) {
                    alert("Tənzimləmələri yadda saxlamaq mümkün olmadı");
                }
            }

            async function loadLogs() {
                try {
                    const res = await fetch('/api/logs');
                    const data = await res.json();
                    const el = document.getElementById('logs-container');
                    el.innerText = data.logs;
                    el.scrollTop = el.scrollHeight; // Auto-scroll to bottom
                } catch (e) {
                    document.getElementById('logs-container').innerText = "Logları yükləmək mümkün olmadı.";
                }
            }

            // Periodically refresh stats
            setInterval(() => {
                if (document.getElementById('section-dashboard').classList.contains('active')) {
                    loadStats();
                }
            }, 5000);

            // Initial load
            loadStats();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)
