# Auto Upload ไป TorrentDD

ตัวอ่านรองรับฟอร์ม `https://www.torrentdd.com/upload.php` และใช้คิวอัปโหลดกลาง
ร่วมกับ Dedbit/Bitsuse ต้นทางยังเป็นงาน BearBit ที่โหลดครบและยืนยัน listing/โซนแล้ว
ใช้ชื่อ คำอธิบาย และรูปจาก BearBit เป็นข้อมูลหลัก โดยคง `[quote]…[/quote]`
และไม่เติมลิงก์เครดิต BearBit ลงคำอธิบายที่เผยแพร่

## โซนและหมวด

| โซน BearBit ต้นทาง | โซน TorrentDD ปลายทาง | หมวดปลายทาง |
|---|---|---|
| `normal` | `normal` — `browse.php` | `1`–`33`, `40` |
| `special` | `special` — `browse18.php` | `34`–`38` |

ตรวจหมวดจากฟอร์มปัจจุบันเสมอ หมวดที่ใช้บ่อยมีดังนี้:

| ID | หมวด |
|---|---|
| `18` | ภาพยนตร์ 4K |
| `19` | ภาพยนตร์ Hi-Def/Blu Ray |
| `20` | ภาพยนตร์ DVD |
| `28`–`32` | ซีรีส์ตามหมวดที่เว็บแยกไว้ |
| `34` | Uncensor |
| `35` | Censor |
| `36` | คลิป |
| `37` | การ์ตูน/หนังสือ/ภาพ |
| `38` | Gay/Lesbian |

เลขหมวดเป็นของ TorrentDD ต้องจับคู่กับข้อความ Category ของ BearBit ผ่านกฎที่ระบุชัด
ไม่สามารถส่งหมวดพิเศษจากต้นทางโซนปกติหรือส่งหมวดปกติจากต้นทางโซนพิเศษได้
ต้องเปิดเว็บไซต์และโซนปลายทางใน `SITE[].target_urls` ก่อนใช้โซนนั้น

## ตั้งกฎ

ใน [auto_upload.example.json](../auto_upload.example.json) มีตัวอย่างที่ปิดไว้สำหรับ
โซนปกติหมวด `19` และโซนพิเศษหมวด `35` โดย `source_category` เป็นข้อความให้กรอก
ให้เปลี่ยนเป็นชื่อหมวดที่ตรวจจากงานจริงและตรวจรายละเอียดก่อนเปิด `enable`
กฎเดิม 37 หมวดใน [auto_upload.rules.json](../auto_upload.rules.json) ยังใช้กับ Dedbit/Bitsuse

`fields: {}` จะใช้รูปต้นทางจาก `metadata.picture` เป็น poster เมื่อมีรูป
หากต้นทางไม่มีรูป งานจะขาดข้อมูล poster และยังส่งไม่ได้ สามารถใช้
`configure JOB_ID --file job-fields.json` กำหนดรูปเฉพาะงานได้ เช่น:

```json
{
  "fields": {"poster": "https://images.example/poster.jpg"},
  "confirmations": {"cf1": false, "cf2": false}
}
```

ตัวอย่างนี้ต้องเปลี่ยน URL เป็นรูปจริง และกำหนด confirmations ตามข้อเท็จจริง:

- `cf1`: ยอมรับการรับผิดชอบไฟล์ที่อัปโหลดและ seed จนมีผู้ดาวน์โหลดเสร็จอย่างน้อยหนึ่งคน
- `cf2`: ยอมรับว่าได้อ่านข้อตกลงการอัปโหลดแล้ว

ระบบตรวจว่าค่าทั้งสองเป็น `true` ก่อนส่ง แต่ไม่ยืนยันข้อความเหล่านี้แทนผู้ใช้งาน
ช่อง `code_id` เป็นรหัสสำหรับระบุไฟล์ให้ตรงกับผู้ที่ขอไฟล์ด่วน ปล่อยว่างได้
ช่องนี้ไม่ใช่ CAPTCHA ของการล็อกอิน

## การเตรียม ตรวจ และส่ง

```sh
python auto_upload_cli.py inspect-form --site TORRENTDD --category 19
python auto_upload_cli.py inspect-form --site TORRENTDD --category 35
python auto_upload_cli.py inspect-source --node 'ชื่อโหนด' --hash 'infohashต้นทาง' --url 'https://bearbit.org/details.php?id=123' --zone normal
python auto_upload_cli.py prepare --node 'ชื่อโหนด' --hash 'infohashต้นทาง' --url 'https://bearbit.org/details.php?id=123' --zone normal --target-site TORRENTDD
python auto_upload_cli.py show JOB_ID
python auto_upload_cli.py check-job JOB_ID
```

`inspect-form` หาโซนจากหมวดที่รองรับและแสดงช่องกรอกโดยไม่แสดง passkey
`prepare` จะสร้างงานแยกปลายทาง หากตั้งทั้ง Dedbit และ TorrentDD สำหรับต้นทางเดียวกัน
ให้ใช้ `--target-site` เลือกปลายทาง ส่วน `run` จะเตรียมงานแต่ละเว็บตามกฎที่เปิดไว้
งานเดิมของ Dedbit/Bitsuse จะไม่ปิดกั้นการสร้างงาน TorrentDD ของ hash ต้นทางเดียวกัน

การตรวจโซนต้นทางลองชื่อ release ที่คงรหัสตอนก่อน แล้วลองชื่อแสดงผลภาษาอังกฤษ/ไทย
เช่นไฟล์ใช้ `S01E10` แต่ BearBit แสดง “ตอน 10/40” ยังต้องพบ ID ต้นทางตรงกันในโซน
ที่เปิดไว้และ hash ของ `.torrent` ต้องตรงกับ client คำค้นเพียงอย่างเดียวไม่ยืนยันโซน
การตรวจซ้ำบน TorrentDD จะลองชื่อแสดงผลทั้งสองภาษาเพิ่มเติมก่อนสรุปผลด้วย

การตรวจซ้ำค้นหาภายในโซน TorrentDD ที่กำหนด ตรวจทุกหน้าที่เกี่ยวข้องภายในขีดจำกัด
แล้วเทียบ payload ของ canonical torrent หากค้นหาไม่ครบหรือพบรายการที่อาจซ้ำจะหยุดงานนั้น
เมื่อข้อมูลครบและต้องการส่งจริง ใช้สวิตช์ `AUTO_UPLOAD` ตาม
[AUTO_UPLOAD.md](AUTO_UPLOAD.md) และคำสั่ง `publish JOB_ID`

## ฟอร์มและข้อจำกัด

- ตัวอ่านตรวจฟอร์มชื่อ `upload`, multipart POST ไป `takeupload.php` และช่องกรอกที่รองรับ
  อ่าน announce ของบัญชีจากหน้าเว็บและยอมรับ tracker `sv-seed.com/announce.php`
- ชื่อแสดงผลยาวได้สูงสุด 200 ตัวอักษร ใช้วิธีย่อจากชื่อ release ที่รองรับหรือหยุดให้แก้ข้อมูล
  ไม่ตัดชื่อทิ้งตามตำแหน่งโดยอัตโนมัติ
- คำอธิบายเกิน 9,000 ตัวอักษรจะหยุดก่อนส่ง เพื่อป้องกันฟอร์มตัดข้อความเงียบ ๆ
- Poster ต้องเป็น URL สาธารณะและผ่านปุ่มตรวจรูปของเว็บจริง เบราว์เซอร์สำหรับ Auto Upload
  เปิดการแสดงรูปเพื่อให้ตัวตรวจรูปทำงาน การโหลดรูปไม่สำเร็จจะหยุดก่อนส่ง
- หลังส่งต้องยืนยัน listing และดาวน์โหลด canonical torrent มาตรวจ payload/hash/announce ใหม่
  hash ปลายทางต้องต่างจากต้นทาง และยังต้องเพิ่มแบบหยุดไว้พร้อม recheck ก่อน seed
- หาก POST ได้ผลไม่ชัดเจน งานจะเป็น `publication_uncertain` และไม่ส่งซ้ำอัตโนมัติ
  ใช้ `reconcile JOB_ID --url 'https://www.torrentdd.com/details.php?id=456'` หลังตรวจรายการจริง
- การเพิ่มงานจากคิวนี้รองรับ qBittorrent และ torrent v1 ตามระบบเดิม

ทดสอบอ่านฟอร์มและกรอกข้อมูลในเบราว์เซอร์จริงแล้ว แนบ metainfo ทดสอบและผ่านปุ่ม
ตรวจรูปของเว็บ โดยบล็อก URL ที่ส่งอัปโหลดไว้ และไม่ได้เปลี่ยน torrent ใน seedbox
ตรวจว่าฟอร์มที่ถูกเปลี่ยนหลังกรอกจะถูกปฏิเสธก่อนส่ง และทดสอบตัวอ่าน canonical
กับการพบ payload ซ้ำจากรายการ TorrentDD ที่มีอยู่แล้วผ่าน HTTP จริงด้วย

ทดสอบเผยแพร่รายการจริงในโซนปกติหมวดซีรีส์จีน (`30`) แล้ว หลังผู้ใช้ยืนยัน
`cf1`/`cf2` ของงานนั้น ตรวจ canonical torrent, payload และ hash ปลายทาง จากนั้น
ยืนยันงานที่พบใน client สั่ง recheck จนไฟล์ครบ 100% และเริ่ม seed โดยใช้ save path เดิม
บันทึก listing, hash และสถานะ `seeding` ลง mapping ตามผลจริง ไม่มีการส่งอัปโหลดซ้ำ

การทดสอบนี้ใช้เฉพาะงานที่ระบุ สวิตช์ Auto Upload ใน config จริงยังปิดอยู่
โซนพิเศษยังไม่ได้ทดสอบเผยแพร่รายการจริง
