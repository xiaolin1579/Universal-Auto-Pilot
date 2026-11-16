# คอนฟิกโหนดแบบใช้ข้อมูลร่วมกัน

โปรแกรมอ่านได้ทั้ง config เดิมและรูปแบบใหม่ผ่าน `node_config.load_config()`
รูปแบบใหม่ยังใช้ `NODES` เป็นรายการ client แต่แยกบัญชี ดิสก์ และนโยบาย Cleaner
ไปเก็บครั้งเดียวใน `CREDENTIALS`, `STORAGES` และ `CLEANER`

ดูตัวอย่าง JSON ที่ใช้งานกับตัวอ่านใหม่ได้ใน [nodes.example.json](../nodes.example.json)
ตัวอย่างนี้เป็นเฉพาะส่วนโหนด ต้องนำไปรวมกับ `SITE`, `SETTING` และค่าระบบอื่น
ใน config เดิม กรอกบัญชี/URL จริงและเปิดเฉพาะ node ที่ต้องการก่อนใช้งาน

## บัญชีและการเชื่อมต่อ

`CREDENTIALS` เก็บ `username` และ `password` โดยใช้ชื่ออ้างอิงที่กำหนดเอง
เช่น `readyidc` โหนดใช้ `auth.http_basic.credential_ref` สำหรับ reverse proxy
และ `auth.session.credential_ref` สำหรับ session ของโปรแกรม

- qBittorrent ใช้ `auth.session.type: "qbit"` และระบุ HTTP Basic เพิ่มได้
  บัญชี Basic และบัญชีในโปรแกรมสามารถเป็นคนละบัญชีกัน
- rTorrent ใช้ `auth.http_basic` ตัว client รองรับ Digest fallback ตามเดิม
- Deluge ใช้ `auth.session.type: "deluge_web"` กับ credential ของ Web UI และมี HTTP Basic แยกได้
  รองรับ `daemon_id` เมื่อ Web UI มีหลาย daemon ดู [คู่มือ Deluge](DELUGE.md)
  และยังไม่รองรับ `auth.session`
- OnlyOneSeedbox ใช้ `auth.session.type: "onlyone_sso"` กับ qBittorrent
  โดย `credential_ref` เป็นบัญชีผู้ให้บริการ ไม่ตั้ง HTTP Basic ร่วมกับ SSO
  `origin` เป็นตัวเลือกสำหรับตรวจสอบ origin ของบริการ ต้องอยู่ใน origin
  ที่ขั้นตอน SSO รองรับ ได้แก่ origin ของโหนดและศูนย์กลาง OnlyOne ที่รู้จัก

บัญชีจะถูกเติมเป็นค่าที่ client เดิมต้องใช้เฉพาะในหน่วยความจำ โปรแกรมไม่เขียน
ค่าที่แปลงแล้วกลับลง config และไม่คัดลอกรหัสผ่านลง mapping
ตัวอ่านปฏิเสธ reference ที่ไม่มีอยู่ รวมถึงฟิลด์ legacy ที่ขัดกับค่าจาก reference

## ดิสก์และแหล่งข้อมูลพื้นที่

โหนดที่ใช้ filesystem/โควตาบัญชีเดียวกันให้ใช้ `storage_id` เดียวกัน
ใน `STORAGES` มีค่าต่อไปนี้:

| ค่า | ความหมาย |
|---|---|
| `disk_type` | `HDD`, `SSD`, `NVME` หรือ `HYBRID` |
| `quota_gib` | ขนาดที่ตั้งไว้ หน่วย GiB; API quota จริงมีความสำคัญก่อนเมื่อใช้ provider reader |
| `safety_buffer_gib` | พื้นที่สำรอง ค่าเริ่มต้น 15 GiB |
| `telemetry.type` | `swizzin`, `onlyone` หรือ `client` |
| `telemetry.via_node` | `id` ของโหนดที่ให้ข้อมูลพื้นที่ ต้องอ้าง storage เดียวกัน |
| `telemetry.mount` | mount สำรองสำหรับ Swizzin ค่าเริ่มต้น `/` |

Swizzin เลือก quota ก่อน และใช้ mount เมื่อไม่มี quota ตาม [STORAGE.md](STORAGE.md)
`via_node` ใช้ origin และบัญชี HTTP ของโหนดต้นทางผ่าน HTTP transport แยก
จึงไม่ส่ง cookies หรือบัญชีของ client ปัจจุบันข้ามไปยัง panel
โหนดต้นทางที่ปิดการรับงานยังใช้อ้างอิงอ่านข้อมูลพื้นที่และตรวจเจ้าของไฟล์ได้
การปิด client ไม่ใช่หลักฐานว่าไม่มีไฟล์ที่ต้องป้องกัน

`onlyone` ต้องอ้าง qBittorrent ของตัวเองและใช้ session SSO เดิม
ส่วน `client` ใช้เส้นทางอ่านพื้นที่เดิมของ client รวม API quota เดิมหากใช้ OnlyOne SSO
การตั้ง `storage_id` ช่วยระบุ client ที่ต้องตรวจการใช้ไฟล์ร่วมกันก่อนลบ
ยังไม่ได้เพิ่มระบบจองพื้นที่ร่วมระหว่าง client หรือระหว่างหลายเครื่อง

แต่ละ node เพิ่ม `space_accounting` ได้เมื่อมีวิธีนับพื้นที่ต่างจาก storage กลาง:

```json
{
  "space_accounting": {
    "safety_buffer_gib": 15,
    "preallocated_in_free_space": true
  }
}
```

`preallocated_in_free_space` ใช้เมื่อพื้นที่ของไฟล์ที่จองไว้ถูกนับในพื้นที่ว่างแล้ว
เพื่อไม่หักพื้นที่ดาวน์โหลดซ้ำ ต้องตั้งตามวิธีรายงานพื้นที่ของบริการนั้นจริง

## Cleaner

`CLEANER.enable` เป็นสวิตช์กลาง `CLEANER.defaults.enable` เป็นค่าเริ่มต้นของ
ทุก node และ node สามารถกำหนดทับผ่าน `cleaner.enable` ได้
Cleaner จะทำงานเมื่อสวิตช์กลางและสวิตช์ของ node เปิดทั้งคู่
ส่วน `allow_space_reclaim` คุมการคืนพื้นที่แยกต่างหาก

กำหนดชุดเกณฑ์ใน `CLEANER.policies` แล้วเลือกด้วย `policy`
เกณฑ์ใช้ `min_ratio`, `min_time_minutes`, `max_time_minutes`
ค่าทับกันตามลำดับ **defaults → policy ที่เลือก → cleaner ของ node**
เวลาใช้หน่วยนาทีตามเกณฑ์เดิมของ Cleaner

```json
{
  "cleaner": {
    "enable": false,
    "policy": "conservative",
    "allow_space_reclaim": false
  }
}
```

การเปิดสวิตช์ไม่ข้ามการตรวจ H&R, Quest, งานอัปโหลด และการใช้ไฟล์ร่วมกัน
อ่านกติกาการลบใน [TORRENT_MAPPING.md](TORRENT_MAPPING.md)

## ตรวจสอบและย้ายข้อมูล

ตรวจทั้ง JSON และ references โดยไม่เชื่อมต่อเว็บไซต์หรือ seedbox:

```sh
python node_config.py config.json
python node_config.py nodes.example.json
```

หากใช้ virtual environment ให้ใช้ Python ใน environment นั้น
ตัวตรวจสอบแสดงจำนวน node และข้อผิดพลาดโดยไม่แสดงรหัสผ่าน

ก่อนย้ายให้สำรอง config แล้วนำส่วน `NODES` ใหม่และสามกลุ่มข้อมูลร่วมไปแทน
ส่วนโหนดเดิม ย้ายสวิตช์/เกณฑ์จาก `GLOBAL_CLEAN` เข้า `CLEANER` และลบ root
`GLOBAL_CLEAN` ออกจากไฟล์รูปแบบใหม่ ค่าของระบบ `SITE`, `SETTING`, การแจ้งเตือน,
`CROSS_SEED_EXECUTION` และ `AUTO_UPLOAD` ใช้รูปแบบเดิม

ไม่ผสม node แบบ legacy กับ node แบบ references ในไฟล์เดียวกัน
การเรียกตัวอ่านซ้ำให้ผลเดิม และไม่แก้ไฟล์บนดิสก์
อย่าบันทึก runtime view ที่แปลงแล้วกลับลงไฟล์ เพราะจะทำให้บัญชีกลับมาซ้ำกัน

`id` ต้องไม่ซ้ำและใช้เป็น reference ภายใน config ส่วน `name` ต้องไม่มีช่องว่างหัวท้าย
และต้องไม่ซ้ำกันแม้ต่างเพียงตัวพิมพ์ใหญ่/เล็ก
**ขั้นนี้ต้องคงชื่อ node เดิมไว้** เพราะ mapping, upload jobs และสถิติเดิม
ยังอ้างชื่ออยู่ ระบบเพิ่ม `client_id` เป็น metadata เมื่อ initialize mapping
แต่ยังไม่ได้ย้าย references เก่าทั้งหมดให้เปลี่ยนชื่อแสดงผลได้อย่างอิสระ

## เมนูจัดการ config

`manage_config.sh` และ `manage_config.bat` เรียก [config_editor.py](../config_editor.py)
ตัวเดียวกัน เมนูรองรับทั้ง config เดิมและแบบ references โดยบันทึก raw document
และใช้ตัวอ่าน normalize เพื่อตรวจความถูกต้องเท่านั้น จึงไม่กระจายรหัสผ่านกลับลง
ทุก node หรือเขียน `_storage_reader` ลงไฟล์

```sh
bash manage_config.sh
bash manage_config.sh --validate
bash manage_config.sh --config /path/to/config-test.json --validate
```

บน Windows ใช้ `manage_config.bat` พร้อม arguments เดียวกัน หรือเรียก Python โดยตรง:

```sh
python config_editor.py --config config.json
python config_editor.py --config config.json --validate
```

ตัวเปิดโปรแกรมเลือก Python ใน `venv`/`.venv` ก่อน แล้วจึงค้นหา Python ของระบบ
ต้องใช้ Python 3.10 ขึ้นไป ไม่ติดตั้งหรือถอนแพ็กเกจเมื่อเปิดตัวจัดการ

เมนูแยกการแก้บัญชีเว็บไซต์/โซน การแจ้งเตือน ตัวกรอง Cleaner และ node
สำหรับ config แบบ references มีเมนูจัดการ `CREDENTIALS` และ `STORAGES` เพิ่มเติม
การแก้ข้อมูลร่วมมีผลต่อทุก node ที่อ้างรายการนั้น ตรวจชื่อ node ที่เกี่ยวข้องก่อนบันทึก
รหัสผ่านและ token ไม่แสดงค่าเดิม และกรอกผ่านช่องรับข้อมูลแบบซ่อนตัวอักษร

การบันทึกจะตรวจ config ก่อนแทนที่ไฟล์แบบ atomic และเก็บฉบับก่อนแก้ไว้ที่
`config.json.editor.bak` หากเลือกไฟล์ชื่ออื่น backup จะใช้ชื่อไฟล์นั้นตามด้วย
`.editor.bak` ไฟล์ backup มีข้อมูลบัญชีและถูกยกเว้นจาก Git เช่นเดียวกับ config
หากพบว่ามีโปรเซสอื่นแก้ config ระหว่างเปิดเมนู จะปฏิเสธการบันทึกทับ

การลบบัญชี/storage หรือ node ที่ยังถูก `via_node` อ้างถึงจะต้องแก้ references
ให้ครบก่อน จึงจะบันทึกได้ การเปลี่ยน `id`/ชื่อของ node เดิมยังไม่เปิดให้ทำผ่านเมนู
เพราะ mapping และงานเก่าต้องใช้ชื่อเดิม การแก้ไฟล์ config ไม่ลบ torrent หรือไฟล์บน seedbox

รีโมทยังคงแก้และบันทึก raw config เฉพาะค่าระบบที่รองรับ จึงรักษา references ไว้เช่นกัน
