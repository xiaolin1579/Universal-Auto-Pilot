# อัปโหลดจาก BearBit และ Cross-Seed

เส้นทางที่รองรับคือ **BearBit โซนปกติ → Dedbit** และ **BearBit โซนพิเศษ → Bitsuse**
ระบบอ่านรายละเอียดจาก BearBit เป็นข้อมูลหลัก ใช้ชื่อ คำอธิบาย และรูปภาพจากต้นฉบับ
โดยแปลง HTML เป็น BBCode และตัด script, form และลิงก์ที่มีข้อมูลยืนยันตัวตนออก
เก็บ URL ต้นทางไว้ภายใน mapping และใช้ `[quote]…[/quote]` สำหรับบล็อกอ้างอิง
ไม่เติมเครดิตหรือลิงก์ BearBit ลงคำอธิบายที่เผยแพร่
ไม่คัดลอก passkey ของ BearBit ไปยังเว็บปลายทาง

ระบบเริ่มต้นปิดอยู่ทุกขั้น คัดลอกเฉพาะส่วน `AUTO_UPLOAD` จาก
[`auto_upload.example.json`](../auto_upload.example.json) ไปเพิ่มใน `config.json` เดิม
อย่านำตัวอย่างไปแทนที่ config ทั้งไฟล์ ตัวอย่างยังไม่ใช่กฎที่เปิดใช้งานได้ทันที
ต้องกรอกหมวดและรายละเอียดให้ตรงกับงานที่จะส่งก่อน
มีชุดกฎเริ่มต้น 37 หมวดที่ตรวจจากเว็บจริงใน
[`auto_upload.rules.json`](../auto_upload.rules.json) และตาราง
[UPLOAD_CATEGORY_RULES.md](UPLOAD_CATEGORY_RULES.md) โดยช่องที่ต้นฉบับไม่ให้ข้อมูลจะเว้นว่าง

## หมวดและฟอร์มจริง

ตรวจรูปแบบฟอร์มจาก URL เหล่านี้:

- Dedbit: `https://www.dedbit.com/upload.php`
- Bitsuse: `https://www.bitsuse.com/bs_upload.php`

ทั้งสองเว็บใช้ฟอร์ม `frmupload` ส่ง multipart ไป `takeupload.php` มี CSRF และ announce
เฉพาะบัญชี ระบบอ่านค่าใหม่ก่อนส่งแต่ละครั้งและใช้ session เดียวกันกับที่ล็อกอิน
หาก action, หมวด, ช่องกรอก หรือ checkbox เปลี่ยนไป ระบบจะหยุดงานนั้นเพื่อให้ตรวจสอบ

กฎหนึ่งรายการจับคู่ `source_zone` และ **ข้อความในแถว Category ของหน้ารายละเอียด BearBit**
กับ `target_site` และ `target_category` ของปลายทาง ต้องมีกฎที่เปิดใช้งานตรงกันเพียงหนึ่งข้อ
ไม่ใช้เลขหมวดของ BearBit เป็นเลขหมวดปลายทาง และไม่เลือกค่าหมวดย่อยตัวแรกให้อัตโนมัติ

Dedbit มีหมวดปกติ เช่น `35` ภาพยนตร์, `7` ซีรีส์, `22` การ์ตูนอนิเมะ ส่วน Bitsuse
มีหมวดพิเศษ `180`–`184` รายการหมวดย่อยต้องตรงกับฟอร์มปัจจุบันทุกช่อง เช่น Dedbit
หมวด `35` ใช้ `c3dub`, `c3sub`, `c35res` โดยความละเอียดมีค่า `720i/p`, `1080i/p`,
`2160i/p`, `SD (DVD/VCD)` ห้ามนำค่าตัวอย่างไปใช้กับงานที่ข้อมูลไม่ตรงกัน

`confirmations` ใช้บันทึกการยืนยันข้อความตาม checkbox ของเว็บสำหรับกฎนั้น:
`ack2` คือไม่เกี่ยวข้องกับการเมือง/ประชาสัมพันธ์ MLM และ Dedbit มี `ack4`
เกี่ยวกับเงื่อนไขเนื้อหาของหมวดปกติ ต้องกำหนดตามข้อเท็จจริงของงานเอง
ส่วน `ack3` ที่ยืนยันไม่พบไฟล์ซ้ำ ระบบเลือกได้หลังค้นหาทุกหน้าที่เกี่ยวข้องและตรวจ payload
ของผลลัพธ์สำเร็จเท่านั้น หากค้นหาไม่ครบหรือดาวน์โหลด metadata ของผลลัพธ์ไม่ได้จะไม่ส่งฟอร์ม

## วิธีทำงาน

1. ตรวจ listing ของ BearBit กับ hash จริง และยืนยันว่าพบ listing ในหน้าค้นหาของโซนที่กำหนด
2. ตรวจว่าต้นทางโหลดครบ พร้อมตรวจขนาดรวมและชื่อ/ขนาดไฟล์ทุกไฟล์จาก client สด
3. สร้าง `.torrent` สำหรับปลายทางโดยคงชื่อไฟล์ ขนาด piece length และ piece hashes
   แล้วกำหนด `info.source` แยกตามเว็บ/โซน/หมวด และ `private=1` จึงได้ infohash ต่างจากต้นทาง
   การเปลี่ยน announce เพียงอย่างเดียวไม่ทำให้ infohash เปลี่ยน
4. บันทึกงาน `draft` หากยังมีช่องหรือ checkbox ที่ขาด หรือ `prepared` เมื่อข้อมูลครบ
   พร้อมป้องกันต้นทางจาก Cleaner ค้นหาซ้ำก่อนส่ง แล้วบันทึก `submitting`
   ลง mapping ก่อน POST หากผลไม่ชัดเจนจะเป็น `publication_uncertain` และไม่ส่งซ้ำอัตโนมัติ
5. หลังเว็บตอบกลับหน้ารายละเอียดที่มี ID จึงบันทึก `published` แล้วดาวน์โหลด `.torrent`
   ของ listing นั้นมาตรวจ payload อีกครั้ง ใช้ **hash ที่เว็บส่งกลับจริง** เป็นตัวอ้างอิง
   หากเว็บแก้ info hash ยังต้องต่างจากต้นทางและ variant อื่น และ payload ต้องเหมือนต้นฉบับ
6. เพิ่ม torrent ปลายทางลง client ต้นทางแบบหยุดไว้ ใช้ save path เดิมและไม่ข้ามตรวจ hash
   สั่ง recheck และติดตามผลในรอบถัดไป ก่อนเริ่ม seed ตามค่าที่เปิดใช้งาน

รองรับ v1 และ qBittorrent ก่อน รองรับ padding ที่ประกาศ `attr=p` โดยยืนยันขนาดรวมเต็มและไฟล์ปกติทุกไฟล์
แม้ qBittorrent ไม่แสดง padding ใน files API หากเป็น v2/hybrid, symlink หรือ client
ที่ยังไม่มี capability นี้จะหยุดขั้นตอน ไม่เดาว่าไฟล์ตรงกัน rTorrent ยังคงใช้ระบบเดิมได้
แต่ยังไม่รองรับการเพิ่มงานจากคิวอัปโหลดนี้

ค่าต่าง ๆ แยกเปิดได้: `enable` เปิดรอบทำงาน, `enable_publish` อนุญาตส่งฟอร์ม,
`enable_seed` อนุญาตเพิ่มงานและ recheck, `enable_resume` อนุญาตเริ่ม seed หลังตรวจครบ
การปิดหรือแก้กฎจะหยุดการส่งงานเก่าที่ผูกกับกฎนั้น รอบบอทหลักเรียกคิวหลังการค้นหาปกติ/
Cross-Seed และจำกัดจำนวนตาม `max_jobs_per_cycle` ห้ามรันบอทหลักและ CLI พร้อมกันบน
browser profile เดียวกัน

การตรวจซ้ำเลือกเฉพาะแถวผลค้นหา ไม่ใช้ลิงก์รายการล่าสุดในส่วนบัญชี
ใช้ชื่อไฟล์ต้นทางก่อน และเก็บเลขตอนแม้มีป้ายความละเอียดนำหน้า เช่น `4K.S01E10`
พร้อมตรวจชื่อแปลจากเว็บหลัก Dedbit ใช้คำค้นแบบวลีในเครื่องหมายคำพูด
ที่ตรวจการทำงานกับกรณีควบคุมจริงแล้ว กรองรายการที่เป็นคนละชื่อ/คนละตอนที่ระบุชัดเจน
หากพบ piece stream เดียวกันแม้เปลี่ยนชื่อจะถือว่าซ้ำ หาก manifest ของไฟล์ปกติตรงกัน
แต่ padding/piece length ต่างกัน หรือพบไฟล์ใหญ่เดียวกันใน pack จะเป็น `possible_duplicate`
และหยุดรอตรวจสอบ ไม่ยืนยันว่าไม่มีรายการซ้ำ

## คำสั่ง

ใช้ Python ที่ติดตั้ง dependencies ของโครงการ เช่น `venv/bin/python` แทน `python`:

```sh
python auto_upload_cli.py status
python auto_upload_cli.py inspect-form --site DEDBIT --category 35
python auto_upload_cli.py inspect-form --site BITSUSE --category 181
python auto_upload_cli.py inspect-source --node 'ชื่อโหนด' --hash 'infohashต้นทาง' --url 'https://bearbit.org/details.php?id=123' --zone normal
python auto_upload_cli.py prepare --node 'ชื่อโหนด' --hash 'infohashต้นทาง' --url 'https://bearbit.org/details.php?id=123' --zone normal
python auto_upload_cli.py show JOB_ID
python auto_upload_cli.py check-job JOB_ID
python auto_upload_cli.py configure JOB_ID --file job-fields.json
python auto_upload_cli.py publish JOB_ID
python auto_upload_cli.py seed JOB_ID
python auto_upload_cli.py run --once
python auto_upload_cli.py run --interval 300
```

`inspect-form` แสดงช่องกรอกและตัวเลือกหมวดย่อยจากเว็บจริงโดยไม่แสดง announce/passkey
ส่วน `inspect-source` ตรวจ listing/โซน/ไฟล์สดและแสดงชื่อหมวด BearBit เพื่อใช้ตั้งกฎ
`prepare` อ่านเว็บและเตรียมไฟล์เท่านั้น ยังไม่ส่งฟอร์มหรือเปลี่ยน torrent ใน seedbox
ต้องมีกฎหมวดที่เปิดใช้ ฟอร์มที่กรอกไม่ครบยังเตรียมเป็น `draft` ได้ โดยเก็บรายการ
ช่องที่ขาดและตัวเลือกของเว็บไว้ให้อ่านด้วย `show` งาน `draft` จะส่งขึ้นเว็บไม่ได้ การรัน `run` จะเตรียมงานที่เพิ่งพบก่อน
แล้วจึงดำเนินขั้นถัดไปในรอบต่อไปตามค่าที่เปิดใช้งาน

`check-job` ตรวจ client, ฟอร์ม และค้นหาซ้ำจริงโดยไม่ส่งฟอร์มหรือเพิ่ม torrent
ผลค้นหาและเวลาตรวจถูกบันทึกใน mapping งานจะยังคง `draft` ถ้าข้อมูลไม่ครบ
การส่งจริงจะตรวจ source และรายการซ้ำใหม่เสมอ จึงไม่ใช้ผลตรวจเก่าเป็นหลักฐานถาวร

ใช้ `configure` เพื่อเติมค่าเฉพาะงานผ่านไฟล์ JSON เช่น:

```json
{
  "fields": {"c35res": "1080i/p"},
  "confirmations": {"ack2": false, "ack4": false}
}
```

กำหนดเฉพาะค่าที่ทราบจริง คำสั่งนี้รับช่องที่เว็บประกาศและ `ack2`/`ack4` ของงานนั้น
ตรวจค่ากับตัวเลือกในฟอร์ม ไม่รับการตั้ง `ack3`, token หรือเปลี่ยนเว็บ/หมวดผ่าน override
ค่าเฉพาะงานไม่เปลี่ยนกฎกลางและไม่กระทบงานอื่น ถ้าข้อมูลครบจึงเปลี่ยนเป็น `prepared`
`confirmations: false` ในตัวอย่างหมายถึงยังไม่ยืนยันข้อความนั้น ไม่ใช่การอนุญาตให้ส่ง

หากส่งฟอร์มแล้วการเชื่อมต่อขาด ให้ตรวจ listing จริงบนเว็บก่อน แล้วระบุ URL เพื่อกู้สถานะ:

```sh
python auto_upload_cli.py reconcile JOB_ID --url 'https://www.dedbit.com/details.php?id=456'
python auto_upload_cli.py reconcile-seed JOB_ID
python auto_upload_cli.py cancel JOB_ID
```

`reconcile` รับเฉพาะงาน `submitting`/`publication_uncertain` และตรวจ canonical torrent
ก่อนยอมรับ `cancel` รับเฉพาะงาน `draft`/`prepared` ที่ยังไม่ส่งเท่านั้น คิวอัตโนมัติจะไม่เตรียมงานที่ยกเลิกซ้ำ
หากต้องแก้กฎแล้วเตรียมใหม่ให้เรียก `prepare` โดยตรงเพื่อยืนยันต้นทางใหม่ งาน `adding`/`seed_uncertain`
จะไม่เพิ่มซ้ำ ใช้ `reconcile-seed` เพื่อตรวจงานที่มีอยู่จริงและสั่ง recheck ใหม่โดยไม่เพิ่มงาน
หากไม่มี hash ปลายทางใน client จะคงสถานะเดิมไว้ให้ตรวจสอบ

## ข้อมูลใน mapping และ Cleaner

เพิ่ม `upload_jobs` และ `content_groups` ลง schema 2 โดยคงข้อมูลเดิมไว้
`content_group_id` คำนวณจาก v1 layout และ piece hashes ไม่จัดกลุ่มด้วยชื่อหรือขนาดรวมอย่างเดียว
เมื่อยืนยัน canonical torrent แล้ว จะเพิ่ม variant ใน `torrents`, เชื่อม `site_records`
และเก็บภาระ H&R/Quest ของปลายทางเป็น `unknown` จนตัวอ่านเว็บจะยืนยันจริง

งานระหว่างดำเนินการจะป้องกันทั้ง hash ต้นทางและปลายทางจาก Cleaner แม้งานเก่าจะไม่มี
H&R แล้ว หลังยืนยันและเริ่ม seed จะปล่อย hold ของคิว ส่วน guard ไฟล์ร่วมและภาระ H&R/
Quest ยังคงทำงานตามปกติ การค้างในสถานะไม่แน่นอนจะคง hold ไว้เพื่อรอตรวจสอบ

ไฟล์ `.torrent` มี passkey จึงเก็บใน `upload_artifacts/` ที่ gitignore และใช้สิทธิ์ `0600`
ไม่บันทึก CSRF, cookies หรือ announce ที่มี passkey ลง mapping ห้ามนำโฟลเดอร์นี้ขึ้น Git
ไฟล์ทดสอบยังเก็บเฉพาะเครื่องตามแนวทางของโครงการ
