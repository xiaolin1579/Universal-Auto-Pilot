# Deluge ผ่าน Web UI / JSON-RPC

รองรับโหนด `type: "deluge"` ผ่าน Web UI เช่น `https://seedbox.example/deluge/`
หรือ `http://localhost:8112/` ไม่ใช้ daemon RPC พอร์ต 58846 โดยตรง
ใช้ `requests` ที่มีใน dependencies เดิม ไม่ต้องติดตั้ง Deluge/Python 2 บนเครื่องที่รันบอท

## ขอบเขตเวอร์ชัน

ตรวจ API ต้นฉบับและทดสอบ fixtures ของ Deluge 1.3.15, 2.0.5, 2.1.1 และ 2.2.0
adapter เลือกคำสั่งจาก `system.listMethods` แทนการตัดสินจากเลขรุ่นอย่างเดียว
จึงใช้กับ Web API ของ 1.3.x/2.x ที่มีความสามารถและข้อมูลสถานะตามที่กำหนดได้
ไม่มีการรับรอง Deluge ทุก patch หรือ Web UI ของรุ่นเก่าที่ใช้ API คนละแบบ
รุ่นอื่นจะตรวจความสามารถก่อนทำงาน และหยุดเมื่อยืนยันข้อมูลที่จำเป็นไม่ได้

ความต่างที่จัดการไว้:

- สถานะ daemon: 1.3.x ส่ง 5 ค่า ส่วน 2.x ส่ง 3 ค่า
- pause/resume: เลือกคำสั่งแบบ plural ของ 2.x หรือคำสั่งเดิมแบบรับรายการของ 1.3.x
- เพิ่ม torrent: เลือก `core.add_torrent_file_async` เมื่อมี หรือ `core.add_torrent_file`
- ตำแหน่งข้อมูล: รองรับ `download_location` และ `save_path`; ถ้าตอบสองค่าไม่ตรงกันจะหยุด
- ทุกคำสั่งเจาะจง hash ไม่ส่งรายการว่างที่อาจตีความว่าทำกับ torrent ทั้งหมด

## คอนฟิก

ใช้ [deluge.node.example.json](../deluge.node.example.json) เป็นส่วนประกอบเพิ่มใน config เดิม
ตัวอย่างปิด node และ Cleaner ไว้ ต้องเปลี่ยน URL และข้อมูลบัญชี ไม่ใช้แทน config ทั้งไฟล์

`auth.session.type` ใช้ `deluge_web` และ `credential_ref` อ้างรหัสผ่าน Web UI
ช่อง username ใน credential นี้ไม่ใช้ยืนยันตัวตนกับ Deluge จึงเว้นว่างได้
ถ้ามี HTTP Basic ของ seedbox/reverse proxy ให้ตั้ง `auth.http_basic` แยกต่างหาก
ถ้าเปิดพอร์ต Web UI โดยตรงและไม่มี HTTP Basic ให้ลบ `auth.http_basic` ออก
หากรหัสผ่านทั้งสองชั้นเหมือนกัน สามารถใช้ credential reference เดียวกันได้

URL ใช้ราก Web UI หรือ endpoint `/json` โดยตรงได้ ห้ามใส่รหัสผ่านหรือ query ใน URL
การเชื่อมต่อ HTTPS ตรวจ certificate และไม่ตาม HTTP redirect พร้อมข้อมูลยืนยันตัวตน

ถ้า Web UI มี daemon เดียว บอทเลือก ID นั้นได้ หากมีหลาย daemon ต้องระบุ `NODES[].daemon_id`
ด้วย host ID จาก Deluge connection manager จะไม่เลือกตัวแรกหรือสลับออกจาก daemon ที่เชื่อมอยู่เอง
ตรวจว่า daemon ที่เลือกยังเป็นตัวเดิมก่อนคำสั่ง core ทุกครั้ง
ควรใช้ Web UI สำหรับ daemon ที่กำหนด และไม่สลับ daemon จากหน้าจออื่นระหว่างทำงาน
การตรวจชื่อก่อนส่งไม่สามารถล็อกการเปลี่ยน daemon จากผู้ใช้อื่นบนเซิร์ฟเวอร์ได้

คอนฟิกเดิมแบบ flat ใช้ `deluge_user: ""`, `deluge_pass`, `url`, `type: "deluge"`
และ `daemon_id` เมื่อจำเป็น ส่วน HTTP Basic ใช้ `http_basic_user`/`http_basic_pass`
จัดการ node แบบ shared หรือ legacy ได้ผ่าน `manage_config.sh` และ `manage_config.bat` เดิม

## ระบบที่รองรับ

- อ่านรายการ สถานะ ขนาด พื้นที่จริง และรายการไฟล์ทุกไฟล์
- รับไฟล์ torrent จาก Normal Search และป้องกันการเริ่มงานเดิมซ้ำโดยอัตโนมัติ
- sync เข้าฐาน mapping กลางโดยใช้ `live_client_inventory`
- Cleaner อ่าน tracker สดและตรวจไฟล์ร่วมทุก client ก่อนเลือกว่าจะลบข้อมูลได้หรือเก็บไฟล์ไว้
- Auto Upload: เตรียมงาน ส่งเว็บ ตรวจ canonical torrent เพิ่มแบบหยุด recheck และ seed

พื้นที่ใช้ `core.get_free_space` (ไบต์) ของ download paths ถ้าตั้ง Swizzin จะเลือก quota ก่อน mount
ตามกติกากลางและใช้พื้นที่จริงเป็นเพดานก่อนหัก safety buffer
การอ่านพื้นที่/รายการที่ล้มเหลวจะไม่ถือว่าไม่มีงานหรือพื้นที่ว่างมากขึ้น

การเพิ่ม hash ใหม่ใน Cross-Seed executor เดิมยังรองรับ qBittorrent เท่านั้น
เมื่อมี node Deluge/rTorrent ที่เปิดอยู่ executor จะหยุดการเพิ่ม hash ใหม่เพื่อไม่สรุปการไม่มีไฟล์ซ้ำ
จาก inventory ที่ตรวจข้าม client ไม่ครบ การค้นหาและอ่าน manifest ยังใช้ได้

## Auto Upload และหลักฐาน recheck

ใช้สวิตช์ `AUTO_UPLOAD.enable`, `enable_publish`, `enable_seed`, `enable_resume` เดิม
รองรับ metainfo v1 เช่นเดียวกับ client อื่น ไม่สร้าง hash ใหม่จากชื่อหรือขนาดไฟล์อย่างเดียว

เพิ่มแบบ `add_paused: true`, `auto_managed: false`, `seed_mode: false`
ปิด `move_completed`, `stop_at_ratio` และ `remove_at_ratio` ของ variant เพื่อใช้ข้อมูลเดิม
โดยตรวจว่าค่าปลายทางถูกใช้จริงก่อนเดินหน้าต่อ ไม่เปลี่ยน options ของงานต้นทาง

Deluge อาจปลด pause ภายในเพื่อทำ `force_recheck` แล้วคืน pause หลังตรวจเสร็จ
adapter ต้องเห็นสถานะ `Checking` สด และบันทึก `recheck_observed` ลง job
จึงยอมรับผลไฟล์ครบและเริ่ม seed เมื่อเปิด `enable_resume`
หากตรวจเสร็จเร็วมากจนไม่เคยสังเกตช่วง Checking จะคง `rechecking`
ไม่ใช้ seed flag เก่าหรือ progress 100% อย่างเดียวเป็นหลักฐานว่าตรวจใหม่แล้ว
สามารถสั่งตรวจใหม่จาก Deluge แล้วให้บอทสังเกตรอบตรวจ ก่อนเริ่ม seed ตามสวิตช์

ถ้าคำสั่งเพิ่มงานหรือ session ตอบไม่ชัดเจน จะไม่ลองคำสั่งเขียนซ้ำ
งานคง `seed_uncertain` ให้ตรวจ hash จริงและใช้ `reconcile-seed` ตามขั้นตอนกลาง
session ที่หมดอายุอนุญาตให้ล็อกอินและลองใหม่เฉพาะคำสั่งอ่านข้อมูลหนึ่งครั้ง

Deluge รุ่นเก่าที่ไม่มีเวลาจบงานจะไม่เดา completion timestamp จากเวลาเพิ่มหรือ seeding time
Cleaner จะเก็บงานที่ยืนยันอายุหลังโหลดเสร็จไม่ได้ไว้ตาม guard เดิม
