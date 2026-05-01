@echo off
title Universal Config Manager (Full System)
setlocal enabledelayedexpansion

:: --- ตรวจสอบ/สร้างไฟล์ config.json เริ่มต้น (ฉบับแก้ไขเครื่องหมายวงเล็บ) ---
if not exist "config.json" (
    echo [!] Initializing config.json...
    (
    echo {
    echo   "SITE": [
    echo   {
    echo     "name": "BEARBIT","username": "", "password": "", "base_url": "https://bearbit.org",
    echo     "target_urls": [
    echo       {"name": "โซนพิเศษ", "url": "https://bearbit.org/viewbrsb.php", "enable": true},
    echo       {"name": "โซนปกติ (No 18+^)", "url": "https://bearbit.org/viewno18sbx.php", "enable": true}
    echo     ]
    echo   },
    echo   {
    echo     "name": "TORRENTDD","username": "", "password": "", "base_url": "https://torrentdd.net",
    echo     "target_urls": [
    echo       {"name": "โซนพิเศษ", "url": "https://torrentdd.net/browse18.php", "enable": true},
    echo       {"name": "โซนปกติ (No 18+^)", "url": "https://torrentdd.net/browse.php", "enable": true}
    echo     ]
    echo   }],
    echo   "TELEGRAM_CONFIG": {"notify_enable": false, "main_bot_token": "", "chat_id": ""},
    echo   "SETTING": {"MIN_SIZE_GB": 15.0, "MAX_SIZE_GB": 150.0, "FREELOAD_ENABLE": true, "MIN_FREE_PERCENT": 0},
    echo   "GLOBAL_CLEAN": {"enable": true, "min_ratio": 1.0, "min_time": 360, "max_time": 1440},
    echo   "NODES": []
    echo }
    ) > config.json
)

:menu
cls
echo ======================================================
echo    Universal Config Manager (Full System)
echo ======================================================
:: ใช้ ^& เพื่อป้องกัน Batch เข้าใจผิดว่าเป็นคำสั่งใหม่
echo  1) Setup Account ^& Zone Management
echo  2) Notification
echo  3) Filter Settings
echo  4) Global Auto-Clean Settings
echo  5) Node Management
echo  6) Exit
echo ======================================================
set /p choice="Select [1-6]: "

if "%choice%"=="1" goto account
if "%choice%"=="2" goto notify
if "%choice%"=="3" goto filter
if "%choice%"=="4" goto global_clean
if "%choice%"=="5" goto node
if "%choice%"=="6" exit
goto menu

:account
cls
echo ======================================================
echo           Select Site to Manage
echo ======================================================
echo  1) BEARBIT 
echo  2) TORRENTDD 
echo  3) Back to Main Menu
echo ======================================================
set "site_choice=" & set /p site_choice="Select [1-3]: "

if "%site_choice%"=="1" set "target_site=BEARBIT" & goto account_menu
if "%site_choice%"=="2" set "target_site=TORRENTDD" & goto account_menu
if "%site_choice%"=="3" goto menu
goto account

:account_menu
cls
echo ======================================================
echo           Management for: %target_site%
echo ======================================================
echo  1) Edit Username/Password
echo  2) Toggle Search Zones (ON/OFF)
echo  3) Back to Site Selection
echo ======================================================
set "acc_choice=" & set /p acc_choice="Select [1-3]: "

if "%acc_choice%"=="1" goto edit_auth
if "%acc_choice%"=="2" goto target_loop
if "%acc_choice%"=="3" goto account
goto account_menu

:edit_auth
cls
echo --- Edit Authentication for %target_site% ---
set /p u="New Username (Enter to skip): "
set /p p="New Password (Enter to skip): "

python -c "import json, sys; site_name='%target_site%'; u=sys.argv[1]; p=sys.argv[2]; d=json.load(open('config.json', encoding='utf-8')); target=next((s for s in d.get('SITE', []) if s['name']==site_name), None); (setattr(target, 'username', u) if u and target else None); (setattr(target, 'password', p) if p and target else None); (json.dump(d, open('config.json', 'w', encoding='utf-8'), indent=2, ensure_ascii=False) if target else print('Site not found'))" "%u%" "%p%"

echo ✅ Process Finished.
pause
goto account_menu

:target_loop
chcp 65001 >nul
cls
echo ======================================================
echo             Target URL Management: %target_site%
echo ======================================================

:: รันแสดงรายการโซนโดยตรงผ่าน Python Command Line (ไม่ต้องสร้างไฟล์)
python -c "import json, sys; site_name='%target_site%'; d=json.load(open('config.json', encoding='utf-8')); target=next((s for s in d.get('SITE', []) if s['name']==site_name), None); [print(f'{i}: [{\"ON\" if u[\"enable\"] else \"OFF\"}] {u[\"name\"]}') for i, u in enumerate(target.get('target_urls', []))] if target else print('Site not found')"

echo ------------------------------------------------------
echo  t) Toggle Enable/Disable ^| b) Back
set /p t_op="Select action: "

if /i "%t_op%"=="b" goto account
if /i "%t_op%"=="t" goto run_toggle
goto target_loop

:run_toggle
set /p t_idx="Enter Index to toggle (e.g., 0): "
if not defined t_idx goto target_loop

:: สั่งสลับค่าใน config.json โดยตรงผ่าน Python Command Line
python -c "import json, sys; site_name='%target_site%'; idx=int(sys.argv[1]); d=json.load(open('config.json', encoding='utf-8')); target=next((s for s in d.get('SITE', []) if s['name']==site_name), None); (target['target_urls'][idx].update({'enable': not target['target_urls'][idx]['enable']}) if target and 0 <= idx < len(target['target_urls']) else None); json.dump(d, open('config.json', 'w', encoding='utf-8'), indent=2, ensure_ascii=False) if target else None" %t_idx%

echo ✅ Updated status for index %t_idx%
pause
goto target_loop

:exit_target_loop
if exist "%PY_TEMP%" del "%PY_TEMP%"
goto account

:notify
cls
echo =======================================
echo       Notification Configuration
echo =======================================

:: --- Telegram ---
echo [ 1. Telegram Settings ]
set "t_en=" & set /p t_en="   Enable Telegram (true/false, Enter to skip): "
set "t_token=" & set /p t_token="   Bot Token: "
set "t_id=" & set /p t_id="   Chat ID: "

:: --- Discord ---
echo.
echo [ 2. Discord Settings ]
set "d_en=" & set /p d_en="   Enable Webhook Notify (true/false, Enter to skip): "
set "d_url=" & set /p d_url="   Webhook URL: "
set "d_admin=" & set /p d_admin="   Admin ID to Mention: "
set "dr_en=" & set /p dr_en="   Enable Remote Bot (true/false, Enter to skip): "
set "dr_token=" & set /p dr_token="   Remote Bot Token: "

:: --- Line ---
echo.
echo [ 3. Line Settings ]
set "l_en=" & set /p l_en="   Enable Line Notify (true/false, Enter to skip): "
set "l_token=" & set /p l_token="   Access Token: "

:: --- Processing with Python ---
python -c "import json, os; \
d=json.load(open('config.json', encoding='utf-8')); \
\
# Update Telegram \
tc=d.get('TELEGRAM_CONFIG', {}); \
t_en=os.getenv('t_en'); t_token=os.getenv('t_token'); t_id=os.getenv('t_id'); \
if t_en: tc['notify_enable'] = (t_en.lower() == 'true'); \
if t_token: tc['main_bot_token'] = t_token; \
if t_id: tc['chat_id'] = t_id; \
\
# Update Discord \
dc=d.get('DISCORD_CONFIG', {}); \
d_en=os.getenv('d_en'); d_url=os.getenv('d_url'); d_admin=os.getenv('d_admin'); \
dr_en=os.getenv('dr_en'); dr_token=os.getenv('dr_token'); \
if d_en: dc['notify_enable'] = (d_en.lower() == 'true'); \
if d_url: dc['webhook_url'] = d_url; \
if d_admin: dc['admin_id'] = d_admin; \
if dr_en: dc['remote_enable'] = (dr_en.lower() == 'true'); \
if dr_token: dc['remote_bot_token'] = dr_token; \
\
# Update Line \
lc=d.get('LINE_CONFIG', {}); \
l_en=os.getenv('l_en'); l_token=os.getenv('l_token'); \
if l_en: lc['enable'] = (l_en.lower() == 'true'); \
if l_token: lc['access_token'] = l_token; \
\
json.dump(d, open('config.json', 'w', encoding='utf-8'), indent=2, ensure_ascii=False)"

echo.
echo ✅ All notifications updated!
pause
goto menu

:filter
cls
echo --- Filter Settings ---
set "MIN_S=" & set /p MIN_S="Min Size (GB) [Enter to skip]: "
set "MAX_S=" & set /p MAX_S="Max Size (GB) [Enter to skip]: "
set "F_EN=" & set /p F_EN="Enable Freeload? (y/n): "
set "F_VAL=0"
if /i "%F_EN%"=="y" (
    set /p F_VAL="Min Free %% (e.g. 50): "
)
python -c "import json, os; d=json.load(open('config.json', encoding='utf-8')); s=d['SETTING']; m_s=os.getenv('MIN_S'); m_a=os.getenv('MAX_S'); f_e=os.getenv('F_EN'); f_v=os.getenv('F_VAL'); s['MIN_SIZE_GB']=float(m_s) if m_s else s.get('MIN_SIZE_GB', 15.0); s['MAX_SIZE_GB']=float(m_a) if m_a else s.get('MAX_SIZE_GB', 150.0); s['FREELOAD_ENABLE']=(True if f_e and f_e.lower()=='y' else False) if f_e else s.get('FREELOAD_ENABLE', True); s['MIN_FREE_PERCENT']=int(f_v) if f_v!='0' else s.get('MIN_FREE_PERCENT', 0); json.dump(d, open('config.json', 'w', encoding='utf-8'), indent=2, ensure_ascii=False)"
echo ✅ Update Complete!
pause
goto menu

:global_clean
cls
echo --- Global Clean Settings ---
set /p g_en="Enable Global Clean (true/false): "
set /p g_ratio="Min Ratio: "
set /p g_min_t="Min Seeding Time (Min): "
set /p g_max_t="Max Seeding Time (Min): "
python -c "import json; d=json.load(open('config.json', encoding='utf-8')); g=d.get('GLOBAL_CLEAN', {}); g['enable']=(True if '%g_en%'.lower()=='true' else False) if '%g_en%' else g.get('enable', True); g['min_ratio']=float('%g_ratio%') if '%g_ratio%' else g.get('min_ratio', 1.0); g['min_time']=int('%g_min_t%') if '%g_min_t%' else g.get('min_time', 360); g['max_time']=int('%g_max_t%') if '%g_max_t%' else g.get('max_time', 1440); d['GLOBAL_CLEAN']=g; json.dump(d, open('config.json', 'w', encoding='utf-8'), indent=2)"
goto menu

:node
cls
echo --- Node Management ---
python -c "import json; d=json.load(open('config.json', encoding='utf-8')); [print(f'{i}: {n[\"name\"]} (Clean: {n.get(\"clean_settings\", {}).get(\"enable\")})') for i, n in enumerate(d.get('NODES', []))]"
set /p op="a) Add | e) Edit | d) Delete | b) Back: "
if "%op%"=="a" goto node_add
if "%op%"=="e" goto node_edit
if "%op%"=="d" goto node_del
goto menu

:node_add
cls
echo --- Add New Node ---
set /p n_name="Name: "
set /p n_type="Type (qbit/rtorrent): "
set /p n_url="URL: "
set /p n_user="User: "
set /p n_pass="Pass: "
set /p n_quota="Quota (GB): "
set /p n_nginx="Nginx Auth (true/false): "

echo -- Clean Settings --
set "nc_en=false"
set /p nc_en="Enable Node Clean (true/false): "

:: ค่าเริ่มต้นสำหรับ Clean Settings
set "nc_ratio=0.5"
set "nc_min_t=120"
set "nc_max_t=720"

:: ถ้าพิมพ์ true ให้กระโดดไปถามรายละเอียดเพิ่ม
if /i "%nc_en%"=="true" (
    set /p nc_ratio="   > Min Ratio (e.g. 0.5): "
    set /p nc_min_t="   > Min Time (Minutes): "
    set /p nc_max_t="   > Max Time (Minutes): "
)

python -c "import json, os; \
d=json.load(open('config.json', encoding='utf-8')); \
node = { \
    'name': '%n_name%', 'type': '%n_type%', 'url': '%n_url%', \
    'qb_user': '%n_user%', 'qb_pass': '%n_pass%', \
    'rt_user': '%n_user%', 'rt_pass': '%n_pass%', \
    'nginx': (True if '%n_nginx%'.lower()=='true' else False), \
    'quota_gb': float('%n_quota%' or 0), 'enable': True, \
    'clean_settings': { \
        'enable': ('%nc_en%'.lower()=='true'), \
        'min_ratio': float('%nc_ratio%'), \
        'min_time': float('%nc_min_t%'), \
        'max_time': float('%nc_max_t%') \
    } \
}; \
d['NODES'].append(node); \
json.dump(d, open('config.json', 'w', encoding='utf-8'), indent=2, ensure_ascii=False)"
goto menu

:node_edit
cls
echo --- Edit Node ---
set /p idx="Enter Index to Edit (0, 1, 2...): "
echo --- Editing Node %idx% ---
set /p n_name="Name (Enter to skip): "
set /p n_url="URL (Enter to skip): "
set /p n_user="User (Enter to skip): "
set /p n_pass="Pass (Enter to skip): "
set /p n_quota="Quota GB (Enter to skip): "
set /p n_nginx="Nginx Auth true/false (Enter to skip): "

echo -- Clean Settings --
set "nc_en="
set /p nc_en="Enable Node Clean true/false (Enter to skip): "

set "nc_ratio="
set "nc_min_t="
set "nc_max_t="

:: ถ้าค่า nc_en ที่กรอกใหม่เป็น true ให้ถามรายละเอียดเพิ่ม
if /i "%nc_en%"=="true" (
    set /p nc_ratio="   > Min Ratio (Enter to skip): "
    set /p nc_min_t="   > Min Time (Enter to skip): "
    set /p nc_max_t="   > Max Time (Enter to skip): "
)

python -c "import json, os; \
d=json.load(open('config.json', encoding='utf-8')); \
idx=int('%idx%'); \
if idx < len(d['NODES']): \
    n=d['NODES'][idx]; \
    if '%n_name%': n['name']='%n_name%'; \
    if '%n_url%': n['url']='%n_url%'; \
    if '%n_user%': n['qb_user']=n['rt_user']='%n_user%'; \
    if '%n_pass%': n['qb_pass']=n['rt_pass']='%n_pass%'; \
    if '%n_quota%': n['quota_gb']=float('%n_quota%'); \
    if '%n_nginx%': n['nginx']=('%n_nginx%'.lower()=='true'); \
    \
    cs=n.get('clean_settings', {'enable':False, 'min_ratio':0.5, 'min_time':120, 'max_time':720}); \
    if '%nc_en%': cs['enable']=('%nc_en%'.lower()=='true'); \
    if '%nc_ratio%': cs['min_ratio']=float('%nc_ratio%'); \
    if '%nc_min_t%': cs['min_time']=float('%nc_min_t%'); \
    if '%nc_max_t%': cs['max_time']=float('%nc_max_t%'); \
    n['clean_settings']=cs; \
    \
    json.dump(d, open('config.json', 'w', encoding='utf-8'), indent=2, ensure_ascii=False); \
    print('✅ Node Updated'); \
else: print('❌ Index not found');"
pause
goto menu

:node_del
set /p idx="Enter Index to Delete: "
python -c "import json; d=json.load(open('config.json', encoding='utf-8')); d['NODES'].pop(%idx%); json.dump(d, open('config.json', 'w', encoding='utf-8'), indent=2)"
goto menu