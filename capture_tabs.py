import subprocess, os

tabs = [
    ('tab-music', 'dany_studio_tab_music.png'),
    ('tab-video', 'dany_studio_tab_video.png'),
    ('tab-colab', 'dany_studio_tab_colab.png'),
    ('tab-master', 'dany_studio_tab_master.png'),
    ('tab-models', 'dany_studio_tab_models.png')
]

with open(r'C:\Users\danin\Music\Dany_AI_Studio\static\index.html', 'r', encoding='utf-8') as f:
    base_html = f.read()

for tab_id, out_name in tabs:
    js_code = f"""
    <script>
    window.addEventListener('DOMContentLoaded', () => {{
        document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
        document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
        const btn = document.querySelector("[data-tab='{tab_id}']");
        if (btn) btn.classList.add("active");
        const pnl = document.getElementById("{tab_id}");
        if (pnl) pnl.classList.add("active");
    }});
    </script>
    """
    injected = base_html.replace('</body>', js_code + '</body>')
    temp_html = rf'C:\Users\danin\Music\Dany_AI_Studio\static\temp_{tab_id}.html'
    with open(temp_html, 'w', encoding='utf-8') as f:
        f.write(injected)
    
    out_path = rf'C:\Users\danin\Music\Dany_AI_Studio\branding\{out_name}'
    chrome_cmd = [
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        '--headless',
        f'--screenshot={out_path}',
        '--window-size=1920,1080',
        temp_html
    ]
    subprocess.run(chrome_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.exists(temp_html):
        os.remove(temp_html)
    print(f"Captured {out_name}: {os.path.exists(out_path)}")
