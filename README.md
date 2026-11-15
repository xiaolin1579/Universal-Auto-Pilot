# Universal Auto-Pilot

ระบบจัดการ torrent บน seedbox ด้วย Python และ **nodriver** สำหรับค้นหาและดาวน์โหลด
ตามโซนของเว็บไซต์ กระจายงานไปยัง qBittorrent/rTorrent ติดตามภาระการ seed และทำ
Cross-Seed โดยใช้ `torrent_mapping.json` เป็นฐานข้อมูลร่วมกัน

## ความสามารถปัจจุบัน

- **ค้นหาและกรอง torrent** ตามเว็บไซต์ โซน ขนาดไฟล์ และเงื่อนไข Freeload
  พร้อมเลือกโหนดตามงานที่กำลังทำ พื้นที่ว่าง และชนิดดิสก์
- **ขอบคุณและโหวต** ตามฟังก์ชันที่เว็บไซต์รองรับ
- **Torrent mapping กลาง** เชื่อมรายการเว็บไซต์กับ infohash ตำแหน่งไฟล์บน client
  ภาระ H&R/Quest Seed ความสัมพันธ์ Cross-Seed และคิวอัปโหลด
- **Cross-Seed** ค้นหาในโซนที่เปิดไว้ของ BearBit, Bitsuse, Dedbit, TorrentDD และ Unlimitz พร้อมลองคำค้น
  สำรองที่คงรหัสตอน/รหัสไฟล์ และตรวจ manifest ก่อนเพิ่มงาน
- **Auto Upload และ Cross-Seed** จาก BearBit โซนปกติไป Dedbit/TorrentDD และโซนพิเศษไป Bitsuse/TorrentDD
  ใช้รายละเอียดจากต้นฉบับ สร้าง infohash แยก และตรวจไฟล์ก่อนเริ่ม seed
- **Cleaner และการคืนพื้นที่** ตรวจภาระการ seed และการใช้ไฟล์ร่วมกันก่อนลบ
  งานที่ยังยืนยันสถานะไม่ได้จะคงการป้องกันไว้
- **อ่านพื้นที่ Swizzin** เลือก quota ของบัญชีก่อน แล้วใช้ mount เมื่อไม่ได้เปิด quota
  รองรับทั้ง shared seedbox และเซิร์ฟเวอร์ที่ติดตั้งเอง
- **รายงานและควบคุมระยะไกล** ผ่าน Telegram และ Discord พร้อมการแจ้งเตือนผ่าน
  Telegram, Discord DM และ LINE Messaging API ตามการตั้งค่า

ความสามารถของแต่ละ client ยังต่างกัน:

| ความสามารถ | qBittorrent | rTorrent |
|---|---|---|
| เชื่อมต่อ อ่านรายการ และเพิ่มงานดาวน์โหลด | รองรับ Web API | รองรับ XML-RPC |
| อ่านพื้นที่ Swizzin แบบ quota → mount | รองรับ | รองรับ |
| ตรวจไฟล์ร่วมก่อน Cleaner ลบ | รองรับ | รองรับ; ระงับการลบหากเก็บไฟล์ไว้ไม่ได้ |
| เพิ่มและ recheck งานจากคิว Auto Upload | รองรับ torrent v1 | ยังไม่รองรับ |

ตัวอ่าน H&R/Quest ที่ยืนยันข้อมูลสดรองรับ **BearBit** ก่อน เว็บไซต์อื่นต้องมีตัวอ่าน
ที่รองรับจึงจะยืนยันสถานะ `clear` ได้ ดูรายละเอียดใน
[Torrent mapping กลาง](docs/TORRENT_MAPPING.md)

## ติดตั้ง

ต้องมี **Python 3.10 ขึ้นไป** และ Google Chrome หรือ Chromium ที่ติดตั้งในระบบ
โปรแกรมใช้ nodriver ควบคุมเบราว์เซอร์ หากติดตั้งเบราว์เซอร์ไว้ในตำแหน่งอื่น
สามารถระบุ executable ผ่านตัวแปรสภาพแวดล้อม `BROWSER_PATH`

### Linux

```bash
git clone https://github.com/xiaolin1579/Universal-Auto-Pilot.git
cd Universal-Auto-Pilot
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

ติดตั้งแพ็กเกจ `python3-venv` ของระบบหากสร้าง virtual environment ไม่ได้
หากต้องการใช้จอเสมือน ให้ติดตั้ง `Xvfb` และตั้ง `SETTING.Xvfb_enable: true`
ค่าเริ่มต้นเมื่อไม่เปิด Xvfb คือรันเบราว์เซอร์แบบ headless

### Windows (PowerShell)

```powershell
git clone https://github.com/xiaolin1579/Universal-Auto-Pilot.git
cd Universal-Auto-Pilot
py -3 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

คำสั่ง `python` ในส่วนถัดไปให้ใช้ Python ใน virtual environment:
Linux ใช้หลัง `source venv/bin/activate` ส่วน Windows ใช้
`.\venv\Scripts\python.exe` แทน

## ตั้งค่า

คัดลอก [config.json.example](config.json.example) เป็น `config.json` แล้วกรอกบัญชี
เว็บไซต์และ seedbox ของตนเอง หากมี config ใช้งานอยู่แล้ว ให้เพิ่มเฉพาะค่าที่ต้องการ
ลงในไฟล์เดิม

ไฟล์ตัวอย่างมีคอมเมนต์ `//` ประกอบคำอธิบาย **ต้องลบคอมเมนต์ออกก่อนใช้งาน**
เพราะตัวอ่านรับ JSON มาตรฐาน เมื่อตั้งค่าแล้วตรวจรูปแบบโดยไม่แสดงข้อมูลบัญชีได้ด้วย:

```bash
python -c "import json; from pathlib import Path; json.loads(Path('config.json').read_text(encoding='utf-8')); print('JSON OK')"
```

โปรแกรมอ่านได้ทั้ง **`NODES` เดิม** และ **รูปแบบ references** ที่ใช้บัญชี/ดิสก์ร่วมกัน
ดูตัวอย่าง [nodes.example.json](nodes.example.json) และวิธีย้ายใน
[NODE_CONFIG.md](docs/NODE_CONFIG.md) ตารางต่อไปนี้อธิบายส่วนตั้งค่าของระบบ:

| ส่วนตั้งค่า | หน้าที่ |
|---|---|
| `SITE` | บัญชีเว็บไซต์ สวิตช์เปิดใช้ และรายการโซนใน `target_urls` |
| `SETTING` | รอบค้นหา ขนาดไฟล์ จำนวนงาน และการใช้ Xvfb |
| `NODES` | ชื่อ/type/URL ของ client บัญชี โควตา และค่าพื้นที่ของแต่ละโหนด |
| `CREDENTIALS` / `STORAGES` / `CLEANER` | บัญชี ดิสก์ และนโยบายร่วมสำหรับ node รูปแบบ references |
| `GLOBAL_CLEAN` / `NODES[].clean_settings` | สวิตช์กลางและเงื่อนไข Cleaner ของแต่ละโหนด |
| `CROSS_SEED_EXECUTION` | อนุญาตเพิ่ม tracker หรือเพิ่มงาน hash ใหม่จาก Cross-Seed |
| `AUTO_UPLOAD` | กฎเส้นทางอัปโหลด หมวดปลายทาง และสวิตช์แต่ละขั้นตอน |
| `TELEGRAM_CONFIG` / `DISCORD_CONFIG` / `LINE_CONFIG` | การแจ้งเตือนและรีโมทตามช่องทางที่รองรับ |

ในรูปแบบเดิม `NODES[].type` ใช้ `qbit` หรือ `rtorrent` พร้อมบัญชี `qb_user`/`qb_pass`
หรือ `rt_user`/`rt_pass` ตามชนิด client โดย `nginx` ใช้เปิด HTTP Basic Auth
สำหรับ qBittorrent ที่อยู่หลัง reverse proxy ส่วน OnlyOneSeedbox ใช้
`provider_sso`, `provider_user` และ `provider_pass` สำหรับล็อกอินผ่านผู้ให้บริการ

เปิดเฉพาะเว็บไซต์ โซน และโหนดที่จะใช้งาน ก่อนเปิดการลบหรือเผยแพร่ให้ตรวจเงื่อนไข
ในแต่ละส่วนด้านล่าง สามารถแก้ config ผ่าน `manage_config.sh` หรือ
`manage_config.bat` ซึ่งใช้เมนู Python ร่วมกันและรองรับทั้ง config เดิมและ references
จัดการบัญชีเว็บไซต์ โซน การแจ้งเตือน ตัวกรอง node บัญชีร่วม storage และ Cleaner ได้
ตัวจัดการตรวจ references ก่อนบันทึกและเก็บข้อมูลในรูปแบบเดิมของไฟล์

```bash
bash manage_config.sh
bash manage_config.sh --validate
```

บน Windows ใช้ `.\manage_config.bat` หรือ `.\manage_config.bat --validate`
เพิ่ม `--config PATH` ได้เมื่อแก้ไฟล์อื่น เช่นไฟล์ทดลอง โดยค่าเริ่มต้นคือ
`config.json` ในโฟลเดอร์โครงการ ดูรายละเอียดใน [NODE_CONFIG.md](docs/NODE_CONFIG.md)

การล็อกอิน **Unlimitz** อ่าน CAPTCHA ผ่าน session ของเบราว์เซอร์โดยตรง จึงใช้ได้
แม้ปิดการแสดงรูป ใช้ OCR สองรุ่นและขอภาพใหม่ได้สูงสุด 5 รอบ โดยส่งข้อมูลบัญชี
เมื่อเว็บยืนยัน CAPTCHA แล้วเท่านั้น จากนั้นตรวจชื่อบัญชีและลิงก์ออกจากระบบบนหน้า
สมาชิก หากเว็บปฏิเสธการล็อกอินจะหยุดรอบนั้นโดยไม่ส่งข้อมูลบัญชีซ้ำ
การค้นหาและตรวจไฟล์ Cross-Seed ของเว็บนี้อธิบายใน
[UNLIMITZ_CROSS_SEED.md](docs/UNLIMITZ_CROSS_SEED.md)

## เริ่มใช้งาน

บน Linux รันจากโฟลเดอร์โครงการหลังเตรียม config และเปิด virtual environment แล้ว:

```bash
python3 -u main.py
```

บน Windows ใช้ `.\venv\Scripts\python.exe -u main.py`

หากต้องการควบคุมผ่าน Telegram/Discord ให้ตั้ง `remote_enable` และข้อมูลบอท
แล้วรันในอีก terminal:

```bash
python remote_control.py
```

ใช้โปรเซสหลักเพียงหนึ่งตัวต่อชุด config/browser profile หากสั่งเริ่มผ่านรีโมท
ให้ตรวจว่าไม่มี `main.py` อีกตัวรันซ้ำอยู่ การแจ้งเตือนและการควบคุมระยะไกล
เปิดแยกกันได้ผ่าน `notify_enable` และ `remote_enable`

ตัวตรวจโปรเซสของรีโมทปัจจุบันอิงคำสั่ง `python`/`python3 -u main.py`
บน Windows จึงอาจรายงานโปรเซส `python.exe` ว่า Offline แม้กำลังทำงาน
ให้ตรวจโปรเซสหลักก่อนสั่ง Start ซ้ำ

การแจ้งเตือน LINE ใช้ Messaging API โดยต้องกำหนด `LINE_CONFIG.enable`,
`access_token` และ `user_id` ของผู้รับเพิ่มเติมจากไฟล์ config ตัวอย่าง

มีสคริปต์ `run_autopilot.sh`/`.bat` และ `run_remote.sh`/`.bat` สำหรับช่วยเริ่มระบบ
สคริปต์ Auto-Pilot มีขั้นตอนถอนแพ็กเกจที่ไม่อยู่ใน `requirements.txt`
จึงควรใช้ใน virtual environment ที่แยกสำหรับโครงการนี้

## Cross-Seed และ Auto Upload

Cross-Seed สำหรับค้นหางานที่มีอยู่บนเว็บ ใช้ inventory และ manifest จริงจาก client
เพื่อตรวจความตรงกันก่อนดำเนินการ ตั้งค่า `CROSS_SEED_EXECUTION` เพิ่มใน config
เมื่อต้องการควบคุมขั้นตอนเพิ่ม tracker และเพิ่มงานใหม่:

```json
{
  "CROSS_SEED_EXECUTION": {
    "enable_tracker_add": false,
    "enable_new_hash_add": false
  }
}
```

TorrentDD รองรับค้นหาแยกโซนปกติ/พิเศษ พร้อมหมวด `cate` และ `freeload=yes`
ตัวอ่านตรวจ File List จากหน้ารายละเอียดก่อนส่งเข้า preflight เดิม
ดูขั้นตอนและข้อจำกัดการเพิ่มงานใน [TORRENTDD_CROSS_SEED.md](docs/TORRENTDD_CROSS_SEED.md)

Auto Upload ใช้ไฟล์ที่โหลดครบแล้วจาก BearBit สร้างรายการใหม่บนเว็บปลายทาง:

| โซนต้นทาง | เว็บปลายทาง |
|---|---|
| BearBit โซนปกติ | Dedbit |
| BearBit โซนพิเศษ | Bitsuse |
| BearBit โซนปกติ | TorrentDD โซนปกติ |
| BearBit โซนพิเศษ | TorrentDD โซนพิเศษ |

เพิ่มส่วน `AUTO_UPLOAD` จาก [auto_upload.example.json](auto_upload.example.json)
ลงใน config เดิม แล้วตั้งกฎหมวดจาก [auto_upload.rules.json](auto_upload.rules.json)
มีชุดกฎเริ่มต้น 37 หมวดสำหรับ Dedbit/Bitsuse โดยช่องที่ข้อมูลไม่ครบต้องเติมตามงานจริง
ตัวอย่าง TorrentDD ใน `auto_upload.example.json` ปิดไว้และต้องกรอกหมวดต้นทางก่อนใช้
ตั้งหลายปลายทางสำหรับ hash ต้นทางเดียวกันได้ โดยแยกงานและตรวจซ้ำต่อเว็บไซต์
สวิตช์ในตัวอย่างเริ่มต้นปิดทุกขั้น:

| สวิตช์ใน `AUTO_UPLOAD` | ขั้นตอนที่อนุญาต |
|---|---|
| `enable` | เปิดรอบคิวอัปโหลด |
| `enable_publish` | ยืนยัน checkbox อัปโหลดที่รองรับอัตโนมัติและส่งฟอร์มหลังตรวจข้อมูล/ไฟล์ซ้ำผ่าน |
| `enable_seed` | เพิ่ม torrent ปลายทางและสั่ง recheck |
| `enable_resume` | เริ่ม seed หลังตรวจไฟล์ครบ |

ตรวจฟอร์มและเตรียมงานได้ก่อนเปิดการเผยแพร่ งานที่ข้อมูลไม่ครบจะเป็น `draft`
ไม่ต้องตั้ง `confirmations` แยกแต่ละกฎ เมื่อเปิด `enable_publish` ระบบยืนยันให้
ทั้งงานใหม่และ draft เดิม โดย `ack3` ยังคงอาศัยผลค้นหาซ้ำจริงเท่านั้น
การเผยแพร่จะค้นหาซ้ำและตรวจต้นทางใหม่ ใช้ canonical torrent ที่ดาวน์โหลดจาก
เว็บปลายทางมาตรวจ payload และ hash ก่อนเพิ่มงานแบบหยุดไว้เพื่อ recheck
หากผลส่งฟอร์มหรือเพิ่มงานไม่ชัดเจน ระบบเก็บสถานะไว้เพื่อ reconcile แทนการส่งซ้ำ

คำสั่งเริ่มต้นสำหรับตรวจคิวและดูตัวเลือก:

```bash
python auto_upload_cli.py --help
python auto_upload_cli.py status
python auto_upload_cli.py inspect-form --site DEDBIT --category 35
python auto_upload_cli.py inspect-form --site BITSUSE --category 181
python auto_upload_cli.py inspect-form --site TORRENTDD --category 19
```

คิวนี้รองรับ **qBittorrent และ torrent v1** รวม padding ที่ประกาศไว้ใน metainfo
ส่วน rTorrent, torrent v2/hybrid และ symlink ยังไม่รองรับในขั้นเพิ่มงานจากคิวนี้
ให้หยุดบอทหลักก่อนใช้ CLI ที่เปิดเบราว์เซอร์ เพื่อใช้ browser profile ครั้งละโปรเซส
อ่านขั้นตอนเตรียม ตรวจ เผยแพร่ seed และกู้สถานะใน [AUTO_UPLOAD.md](docs/AUTO_UPLOAD.md)
และตาราง [กฎหมวดหมู่](docs/UPLOAD_CATEGORY_RULES.md)
ดูข้อจำกัดรูป poster, confirmations และหมวดของ TorrentDD ใน
[TORRENTDD_AUTO_UPLOAD.md](docs/TORRENTDD_AUTO_UPLOAD.md)
ทดสอบเผยแพร่จริงบน TorrentDD โซนปกติหมวดซีรีส์จีนแล้ว พร้อมตรวจ canonical torrent,
recheck จนครบ และเริ่ม seed ด้วยไฟล์เดิมบน client ต้นทาง

## พื้นที่เก็บข้อมูลและ Cleaner

เมื่อระบุ `storage_api: "swizzin"` ใน node ทั้ง qBittorrent และ rTorrent จะอ่าน
Disk Quota Info ของ panel ก่อน หากไม่ได้ติดตั้ง quota จึงอ่าน mount แทน
รวมถึงคำตอบ `Quota not installed` ของ Swizzin ต้นฉบับ การเลือกอิงข้อมูล API
จึงใช้กับ shared seedbox และเซิร์ฟเวอร์ที่ติดตั้งเองได้โดยไม่ผูกชื่อผู้ให้บริการ

`storage_mount` ใช้เฉพาะตอน fallback ค่าเริ่มต้นคือ `/` ถ้าไฟล์อยู่บน mount อื่น
ให้กำหนดให้ตรง เช่น `/home` พื้นที่ใช้ได้คำนวณจากค่าต่ำสุดของแหล่งที่เลือกกับ
พื้นที่จริงจาก client แล้วหัก `storage_safety_buffer_gb` ซึ่งใช้หน่วย GiB
quota เหลือ 0 ถือว่าโควตาหมด ส่วนข้อมูลเสียหรือล็อกอินล้มเหลวจะหยุดอ่านพื้นที่
แทนการใช้ mount ข้ามข้อจำกัดบัญชี ดู [STORAGE.md](docs/STORAGE.md)

Cleaner ตรวจ H&R/Quest, manual hold และงานอัปโหลดที่กำลังดำเนินการก่อนลบ
ผลตรวจที่ยังไม่ทราบหรือหมดอายุใช้อนุญาตให้ลบไม่ได้ การลบไฟล์จริงทำได้เมื่อ
ตรวจข้อมูลสดครบและยืนยันว่าไม่มี torrent อื่นใช้ไฟล์ร่วมกัน รวม client ที่ปิดใช้งาน
หากยังมี Cross-Seed จะเก็บไฟล์ไว้ และ rTorrent ที่ไม่สามารถรับประกันการลบเฉพาะงาน
จะระงับการลบงานนั้น

สำหรับ config เดิม `GLOBAL_CLEAN.enable` เป็นสวิตช์กลาง ส่วน `clean_settings` ใช้ตั้งเงื่อนไขของ node
และ `allow_space_reclaim` ควบคุมการคืนพื้นที่ ดูกติกาภาระการ seed และการป้องกันไฟล์
ใน [TORRENT_MAPPING.md](docs/TORRENT_MAPPING.md)
รูปแบบ references ใช้ `CLEANER` และนโยบายร่วมตาม [NODE_CONFIG.md](docs/NODE_CONFIG.md)

## ไฟล์และเอกสารสำคัญ

| ไฟล์ | หน้าที่ |
|---|---|
| [main.py](main.py) | รอบทำงานหลัก เว็บไซต์ client และ Cleaner |
| [remote_control.py](remote_control.py) | รีโมท Telegram/Discord และรายงาน |
| [client_registry.py](client_registry.py) | จุดลงทะเบียน adapter สำหรับโปรแกรมบิท |
| [torrent_store.py](torrent_store.py) | อ่านและอัปเดต mapping กลางแบบ transaction |
| [cross_seed_search.py](cross_seed_search.py) | คำค้นสำรองและการเลือกโซน Cross-Seed |
| [torrentdd_cross_seed.py](torrentdd_cross_seed.py) | ค้นหา อ่าน File List และดาวน์โหลด metainfo ของ TorrentDD |
| [auto_upload_cli.py](auto_upload_cli.py) | คำสั่งตรวจ เตรียม เผยแพร่ และกู้สถานะอัปโหลด |
| [auto_upload.py](auto_upload.py) / [upload_runner.py](upload_runner.py) | คิวอัปโหลดและการเชื่อมกับรอบบอท |
| [upload_sites.py](upload_sites.py) / [upload_client.py](upload_client.py) | ตัวอ่านเว็บ/ฟอร์มและคำสั่ง client สำหรับอัปโหลด |
| [torrent_metainfo.py](torrent_metainfo.py) | อ่าน metainfo ตรวจ payload และสร้าง variant |
| [storage_telemetry.py](storage_telemetry.py) | ตัวอ่านพื้นที่ Swizzin แบบ quota ก่อน mount |
| [node_config.py](node_config.py) / [nodes.example.json](nodes.example.json) | ตัวอ่านและตัวอย่าง config โหนดแบบใช้ข้อมูลร่วมกัน |
| [config_editor.py](config_editor.py) | เมนูแก้ config ร่วมสำหรับ `manage_config.sh` และ `.bat` |
| `torrent_mapping.json` | ข้อมูล torrent, site records, H&R/Quest, ตำแหน่งไฟล์ และ upload jobs |
| `stats_history.json` / `stats_cache.json` | สถิติและข้อมูลสำหรับรายงาน |
| [docs/TORRENT_MAPPING.md](docs/TORRENT_MAPPING.md) | schema การย้ายข้อมูล ภาระการ seed และการเพิ่ม client |
| [docs/AUTO_UPLOAD.md](docs/AUTO_UPLOAD.md) | ตั้งค่าและใช้งาน Auto Upload/Cross-Seed |
| [docs/TORRENTDD_AUTO_UPLOAD.md](docs/TORRENTDD_AUTO_UPLOAD.md) | หมวด ฟอร์ม และวิธีเตรียมอัปโหลดไป TorrentDD |
| [docs/UPLOAD_CATEGORY_RULES.md](docs/UPLOAD_CATEGORY_RULES.md) | ตารางจับคู่หมวด BearBit กับเว็บปลายทาง |
| [docs/STORAGE.md](docs/STORAGE.md) | กติกาเลือก quota/mount และการตั้งค่าพื้นที่ |
| [docs/NODE_CONFIG.md](docs/NODE_CONFIG.md) | การอ้างบัญชี/ดิสก์ นโยบาย Cleaner และการย้าย config |
| [docs/TORRENTDD_CROSS_SEED.md](docs/TORRENTDD_CROSS_SEED.md) | การค้นหาและ preflight สำหรับ TorrentDD |

สำรอง `torrent_mapping.json` ก่อนย้ายหรือแก้ข้อมูล โครงสร้างปัจจุบันเป็น schema 2
และเก็บข้อมูลเดิมไว้ร่วมกับ `site_records`, `upload_jobs` และ `content_groups`
หลังย้ายข้อมูลแล้ว ตัวอ่าน H&R/Quest ใช้ฐานกลาง การแก้ไฟล์ link DB เดิมจะไม่มีผล
ต่อสถานะที่โปรแกรมใช้งาน

เก็บ `config.json`, ข้อมูลยืนยันตัวตนใน `auth/`/`profiles/`, mapping และ
`upload_artifacts/` ไว้เฉพาะเครื่อง โดยเฉพาะไฟล์ `.torrent` ที่มี passkey
ไฟล์เหล่านี้ไม่ควรอยู่ใน commit
