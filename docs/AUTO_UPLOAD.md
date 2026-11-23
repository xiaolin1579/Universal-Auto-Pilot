# อัปโหลดจาก BearBit และ Cross-Seed

เส้นทางที่รองรับคือ **BearBit โซนปกติ → Dedbit หรือ TorrentDD โซนปกติ** และ
**BearBit โซนพิเศษ → Bitsuse หรือ TorrentDD โซนพิเศษ** สามารถตั้งกฎให้ต้นทางหนึ่งงาน
ส่งไปหลายเว็บไซต์ได้ โดยเก็บงานแยกตาม hash ต้นทางและเว็บปลายทาง
ระบบอ่านรายละเอียดจาก BearBit เป็นข้อมูลหลัก ใช้ชื่อ คำอธิบาย และรูปภาพจากต้นฉบับ
โดยแปลง HTML เป็น BBCode และตัด script, form และลิงก์ที่มีข้อมูลยืนยันตัวตนออก
เก็บ URL ต้นทางไว้ภายใน mapping และใช้ `[quote]…[/quote]` สำหรับบล็อกอ้างอิง
ท้ายคำอธิบายที่ส่งขึ้น Dedbit, Bitsuse และ TorrentDD จะเพิ่มเครดิตเว็บต้นทางและชื่อสคริปต์:

```text
เครดิตต้นฉบับ: เว็บเพื่อนบ้าน
อัปโหลดด้วยสคริปต์: [url=https://github.com/xiaolin1579/Universal-Auto-Pilot]Universal-Auto-Pilot[/url]
```

ใช้กับงานใหม่และงานค้างที่ยังไม่เผยแพร่ โดยเติมตอนกรอกฟอร์มและป้องกันข้อความท้ายซ้ำ
คำอธิบายต้นฉบับใน metadata ยังคงใช้สำหรับตรวจหมวดหมู่ตามเดิม
TorrentDD นับความยาวรวมข้อความเครดิตก่อนตรวจข้อจำกัด 9,000 หน่วย UTF-16
หากเกินจะหยุดให้แก้คำอธิบาย โดยไม่ตัดเนื้อหาทิ้งอัตโนมัติ
ไม่คัดลอก passkey ของ BearBit ไปยังเว็บปลายทาง

ระบบเริ่มต้นปิดอยู่ทุกขั้น คัดลอกเฉพาะส่วน `AUTO_UPLOAD` จาก
[`auto_upload.example.json`](../auto_upload.example.json) ไปเพิ่มใน `config.json` เดิม
อย่านำตัวอย่างไปแทนที่ config ทั้งไฟล์ ตัวอย่างยังไม่ใช่กฎที่เปิดใช้งานได้ทันที
ต้องกรอกหมวดและรายละเอียดให้ตรงกับงานที่จะส่งก่อน
มีชุดกฎเริ่มต้น 37 หมวดสำหรับ Dedbit/Bitsuse ที่ตรวจจากเว็บจริงใน
[`auto_upload.rules.json`](../auto_upload.rules.json) และตาราง
[UPLOAD_CATEGORY_RULES.md](UPLOAD_CATEGORY_RULES.md) โดยช่องที่ต้นฉบับไม่ให้ข้อมูลจะเว้นว่าง
ตัวอย่าง TorrentDD ใน `auto_upload.example.json` ปิดใช้งานไว้และใช้ชื่อหมวดต้นทาง
เป็นข้อความให้กรอก ต้องระบุหมวดจากงานจริงก่อนเปิดใช้ ดูรายละเอียดที่
[TORRENTDD_AUTO_UPLOAD.md](TORRENTDD_AUTO_UPLOAD.md)

## หมวดและฟอร์มจริง

ตรวจรูปแบบฟอร์มจาก URL เหล่านี้:

- Dedbit: `https://www.dedbit.com/upload.php`
- Bitsuse: `https://www.bitsuse.com/bs_upload.php`
- TorrentDD: `https://www.torrentdd.com/upload.php`

Dedbit/Bitsuse ใช้ฟอร์ม `frmupload` ส่ง multipart ไป `takeupload.php` มี CSRF และ announce
เฉพาะบัญชี ระบบอ่านค่าใหม่ก่อนส่งแต่ละครั้งและใช้ session เดียวกันกับที่ล็อกอิน
หาก action, หมวด, ช่องกรอก หรือ checkbox เปลี่ยนไป ระบบจะหยุดงานนั้นเพื่อให้ตรวจสอบ
TorrentDD ใช้ฟอร์มชื่อ `upload` ไม่มี form ID และส่ง multipart ไป `takeupload.php`
ตัวอ่านตรวจรูปแบบช่องกรอกและอ่าน announce ของบัญชีจากหน้าเว็บใหม่ก่อนส่ง

กฎหนึ่งรายการจับคู่ `source_zone` และ **ข้อความในแถว Category ของหน้ารายละเอียด BearBit**
กับ `target_site` และ `target_category` ของปลายทาง ต้องมีกฎที่เปิดใช้งานตรงกันเพียงหนึ่งข้อ
ต่อเว็บไซต์ปลายทาง หากมีหลายปลายทาง คำสั่ง `prepare` ต้องระบุ `--target-site`
ส่วนคิวอัตโนมัติจะเตรียมแต่ละปลายทางแยกกันภายใต้ `max_jobs_per_cycle`
ไม่ใช้เลขหมวดของ BearBit เป็นเลขหมวดปลายทาง และไม่เลือกค่าหมวดย่อยตัวแรกให้อัตโนมัติ

สำหรับ Bitsuse หมวดวิดีโอ `180`/`181` ช่อง `c100type` เป็นประเภทเนื้อหา เช่น `ภาพยนตร์`
ส่วน `c100type2` เป็นประเภทย่อย เช่น `ชาย-หญิง` และ `c100cat` เป็นประเทศ/หมวด เช่น `ญี่ปุ่น`
เลือกค่าตามเนื้อหาจริง `enable_publish` ยืนยัน checkbox ให้ แต่ไม่เติมหมวดย่อยที่ยังว่าง
งานที่ขาดข้อมูลเหล่านี้จะคงเป็น `draft` และแจ้ง “รอข้อมูลเพิ่มเติม” พร้อมชื่อช่องที่ต้องระบุ
งาน `draft` ที่ยังไม่เคยส่งไม่ต้องใช้ `reconcile` ซึ่งมีไว้สำหรับผลส่งที่ยังไม่ยืนยันเท่านั้น
งานเดิมให้กำหนดค่าเฉพาะงานผ่าน `configure JOB_ID --file job-fields.json` เช่น
`{"fields":{"c100type":"ภาพยนตร์","c100type2":"ชาย-หญิง","c100cat":"ญี่ปุ่น"}}`
เมื่อค่าตรงกับงานจริงและครบตามฟอร์มจึงเป็น `prepared` การส่งยังตรวจต้นทางและค้นหาซ้ำตามเดิม
การแก้ `rules[].fields` ใช้กับงานใหม่ ส่วนงานเดิมต้องจัดการแยกเพราะการเปลี่ยนกฎจะหยุดงานเก่าที่ผูกไว้

Dedbit มีหมวดปกติ เช่น `35` ภาพยนตร์, `7` ซีรีส์, `22` การ์ตูนอนิเมะ ส่วน Bitsuse
มีหมวดพิเศษ `180`–`184` รายการหมวดย่อยต้องตรงกับฟอร์มปัจจุบันทุกช่อง เช่น Dedbit
หมวด `35` ใช้ `c3dub`, `c3sub`, `c35res` โดยความละเอียดมีค่า `720i/p`, `1080i/p`,
`2160i/p`, `SD (DVD/VCD)` ห้ามนำค่าตัวอย่างไปใช้กับงานที่ข้อมูลไม่ตรงกัน

เมื่อเปิด `AUTO_UPLOAD.enable_publish: true` ระบบติ๊กเงื่อนไขอัปโหลดที่รองรับอัตโนมัติ
และบันทึกลงค่าเฉพาะงาน ไม่ต้องตั้ง `confirmations` แยกในแต่ละกฎ
config เดิมที่มีค่าเหล่านี้เป็น `false` ยังใช้ได้ โดยสวิตช์เผยแพร่เป็นตัวอนุญาตในรอบส่ง
งาน `draft` เดิมจะถูกพิจารณาใหม่อัตโนมัติ ถ้ายังขาดข้อมูลหมวดหรือรูปจะยังไม่ส่ง
ส่วน `AUTO_UPLOAD.enable` ยังต้องเปิดเพื่อให้คิวทำงาน และสวิตช์ seed/resume ยังแยกตามเดิม

checkbox ที่รองรับมีดังนี้:
`ack2` คือไม่เกี่ยวข้องกับการเมือง/ประชาสัมพันธ์ MLM และ Dedbit มี `ack4`
เกี่ยวกับเงื่อนไขเนื้อหาของหมวดปกติ
ส่วน `ack3` ที่ยืนยันไม่พบไฟล์ซ้ำ ระบบเลือกได้หลังค้นหาทุกหน้าที่เกี่ยวข้องและตรวจ payload
ของผลลัพธ์สำเร็จเท่านั้น หากค้นหาไม่ครบหรือดาวน์โหลด metadata ของผลลัพธ์ไม่ได้จะไม่ส่งฟอร์ม

TorrentDD ใช้ `cf1` ยอมรับการรับผิดชอบ seed จนมีผู้ดาวน์โหลดเสร็จอย่างน้อยหนึ่งคน
และ `cf2` ยอมรับว่าได้อ่านข้อตกลงการอัปโหลดแล้ว การเปิด `enable_publish` อนุญาตให้
ระบบติ๊กสองช่องนี้ด้วย รูป poster ใช้ `metadata.picture` ของ BearBit เป็นค่าเริ่มต้น
หรือกำหนด `fields.poster` เฉพาะงาน เว็บต้องตรวจรูปสำเร็จก่อนส่งฟอร์ม
ช่อง `code_id` เป็นรหัสของผู้ขอไฟล์ด่วน ใส่ได้เมื่อมีข้อมูลและปล่อยว่างได้

## วิธีทำงาน

ชื่อประกาศใช้รูปแบบ `ชื่อหลัก (ปี) SxxExx - ชื่อไทย [คุณภาพ] [เสียง/ซับ]`
TorrentDD/Bitsuse ใช้แบบเต็ม ส่วน Dedbit ใช้แบบย่อที่ตัดชื่อรอง Codec และ FPS
รวมแท็กคุณภาพไว้กลุ่มเดียว ใช้เฉพาะข้อมูลที่ต้นฉบับระบุ และคงตอน/ช่วงตอนหรือรหัสเรื่อง
ข้อมูลปีหรือความละเอียดที่ขัดกันจะหยุดให้กำหนดชื่อเอง ไม่มีการเดาภาษาจากประเทศ
เมื่อเกินเพดานของเว็บจะลดรายละเอียดเป็นกลุ่ม ไม่ตัดชื่อเรื่องกลางคำ

ตัวอย่าง TorrentDD:

```text
The Silent Storm (2026) S01E10 - เงาพายุ [4K WEB-DL H.265 HDR 60fps] [เสียงต้นฉบับ/ซับไทย]
```

ตัวอย่าง Dedbit (ไม่เกิน 80 ตัวอักษร):

```text
The Silent Storm (2026) S01E10 [4K WEB-DL HDR] [ซับไทย]
```

ดูชื่อที่จะใช้ผ่าน `show JOB_ID` ได้ กำหนดชื่อเฉพาะงานที่ยังไม่ส่งผ่านไฟล์ JSON
`{"title": "ชื่อประกาศที่ตรวจแล้ว"}` และคำสั่ง `configure JOB_ID --file job-fields.json`
ชื่อที่กำหนดเองยังต้องไม่เกินเพดานเว็บ การจัดชื่อมีผลเฉพาะประกาศที่ส่งครั้งถัดไป
ไม่เปลี่ยน `info.name`, ชื่อไฟล์ข้อมูล หรือ payload และไม่แก้ประกาศเดิมบนเว็บ

ไฟล์ `.torrent` ที่สร้างใหม่ใช้ชื่อ `ชื่อเรื่อง.ปี.ตอน.คุณภาพ.เว็บปลายทาง.torrent` เช่น:

```text
The.Silent.Storm.2026.S01E10.4K.TORRENTDD.torrent
```

เว้นส่วนที่ต้นทางไม่ระบุออก กรองอักขระที่ใช้ไม่ได้บน Windows และไม่เติม ID หรือ
ชื่อขั้นตอนลง basename แยกเก็บใน `upload_artifacts/<job_id>/<ขั้นตอน>/` โดยแต่ละ
ขั้นตอนใช้ basename เดียวกัน เพื่อให้ source/prepared/submission/canonical ไม่เขียนทับกัน
ไฟล์ที่แนบส่งขึ้นเว็บใช้ชื่อใหม่นี้ด้วย ส่วน artifact เดิมยังอ่านจาก path ที่เก็บใน mapping ได้

1. ตรวจ listing ของ BearBit กับ hash จริง และยืนยันว่าพบ ID ต้นฉบับในตารางผลค้นหาของโซนที่กำหนด
   ค้นทั้งชื่อไฟล์และชื่อประกาศ พร้อมตามหน้าถัดไปที่คงคำค้นและตัวกรองเดิม รวมไม่เกิน 12 หน้า
   ต่องาน หากยังยืนยันไม่ได้จะหยุด ไม่ใช้เพียงลิงก์หมวดหรือรายการในส่วนอื่นของหน้าเป็นหลักฐาน
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
   สั่ง recheck และติดตามผลภายในช่วงรอของรอบนั้น ก่อนเริ่ม seed ตามค่าที่เปิดใช้งาน

รองรับ v1 บน qBittorrent, rTorrent และ Deluge รองรับ padding ที่ประกาศ `attr=p` โดยยืนยันขนาดรวมเต็มและไฟล์ปกติทุกไฟล์
แม้ qBittorrent ไม่แสดง padding ใน files API หากเป็น v2/hybrid, symlink หรือ client
ที่ยังไม่มี capability นี้จะหยุดขั้นตอน ไม่เดาว่าไฟล์ตรงกัน
ระบบตรวจไฟล์ต้นทางก่อนเปิดฟอร์มปลายทาง และแสดงขั้นตอนที่ติดพร้อมเหตุผลที่ปลอดภัยใน log
เช่น `source manifest: v2/hybrid source unsupported; Auto Upload requires v1`
ข้อผิดพลาดที่ไม่รู้จักจะแสดงเฉพาะขั้นตอนและชนิด exception เพื่อไม่เปิดเผย URL หรือ passkey

rTorrent ใช้ adapter XML-RPC ของคิวนี้โดยตรง ไม่เรียกเมธอดเพิ่มงานเดิมที่เริ่มดาวน์โหลดทันที
ตรวจ `system.listMethods` ว่ามีคำสั่งที่จำเป็นก่อนเตรียมงาน และอ่านตัวเลข/flags จาก XML-RPC
อย่างเคร่งครัด รวมข้อมูลขนาดแบบ `i8` การอ่าน API ล้มเหลวจะไม่ถือว่า hash นั้นไม่มีอยู่
การเพิ่มงานใช้ `load.raw` แล้วสั่ง `d.stop` เฉพาะ hash ใหม่ ไม่ข้ามตรวจ hash และไม่เรียก `load.raw_start`
สั่ง `d.check_hash` ขณะงานหยุด และยืนยัน `d.is_hash_checked`, จำนวน pieces, ไฟล์ทุกไฟล์
และตำแหน่งข้อมูล ก่อนเรียก `d.start` เมื่อเปิด `enable_resume` ผล recheck ที่ไฟล์ไม่ครบจะหยุดแจ้งตรวจสอบ

สำหรับ rTorrent ค่า `save_path` ใน job คือ `d.directory` จริงของ client ซึ่งในงานหลายไฟล์
รวมโฟลเดอร์รากของ torrent แล้ว adapter ใช้ `d.directory_base.set` (หรือ `d.directory.base.set`)
เพื่อวาง variant ตรงที่เดิม ไม่ต่อชื่อโฟลเดอร์ซ้ำ และเติมชื่อรากเฉพาะรายการไฟล์ที่ใช้เทียบ manifest
ยังไม่รองรับงานที่เปลี่ยนชื่อโฟลเดอร์รากหลายไฟล์ หรือ directory ที่มีอักขระคำสั่ง เช่น quote,
backslash, `$`, `;`, comma, backtick และอักขระควบคุม โดยจะปฏิเสธก่อนเตรียม/ส่งงาน
path ภาษาไทย ช่องว่าง และ `&` รองรับผ่านการเข้ารหัส XML

เลือก node ที่เป็น `type: "rtorrent"` ได้ด้วย CLI เดิม หรือเปิด node ไว้ให้คิวอัตโนมัติเลือก
ไม่ต้องเพิ่มสวิตช์ Auto Upload ชุดใหม่ ผลเพิ่มงานที่ไม่ยืนยันจะเป็น `seed_uncertain`
และไม่เพิ่มซ้ำอัตโนมัติ คิวกู้ต่อได้เมื่อพบรายการเดิมที่หยุดอยู่และผ่านการตรวจไฟล์ทั้งหมด
หรือใช้ `reconcile-seed` เพื่อตรวจงานที่มีอยู่แล้วและสั่งตรวจ hash ใหม่
โดยไม่ลบ torrent หรือไฟล์จริง

Deluge ใช้ Web JSON-RPC หรือ Daemon SSL-RPC ของ 1.3.15/2.0.x/2.1.x/2.2.x
ผ่าน core adapter เดียวกัน อ่านทุกไฟล์และเก็บข้อมูลเดิมไว้ ดู [การตั้งค่า Deluge](DELUGE.md)
เพิ่มแบบหยุด ปิดการย้ายไฟล์หลังโหลดและการลบ/หยุดตาม ratio ของ variant แล้วสั่ง `force_recheck`
ต้องเห็นช่วง `Checking` และบันทึกหลักฐานนั้นก่อนยอมรับการเริ่ม seed
อ่านขอบเขต API, การเลือก daemon, คอนฟิก และกรณี recheck เร็วจนไม่เห็นสถานะใน [DELUGE.md](DELUGE.md)

ค่าต่าง ๆ แยกเปิดได้: `enable` เปิดรอบทำงาน, `enable_publish` อนุญาตส่งฟอร์ม,
`enable_seed` อนุญาตเพิ่มงานและ recheck, `enable_resume` อนุญาตเริ่ม seed หลังตรวจครบ
การปิดหรือแก้กฎจะหยุดการส่งงานเก่าที่ผูกกับกฎนั้น รอบบอทหลักเรียกคิวหลังการค้นหาปกติ/
Cross-Seed และจำกัดจำนวนตาม `max_jobs_per_cycle` ห้ามรันบอทหลักและ CLI พร้อมกันบน
browser profile เดียวกัน คำสั่ง CLI ที่ทำงานเฉพาะครั้งใช้ profile แยกตาม PID
ส่วน `run` ใช้ profile คิวตามเดิม จึงไม่ควรเปิดหลายตัวพร้อมกัน

### ทำงานต่อจนเริ่ม seed ในรอบเดียว

คิวในบอทหลักและ `auto_upload_cli.py run --once` ทำงานใหม่ต่อทันทีหลังเตรียมเสร็จ:
เตรียมงาน → เผยแพร่ → ดาวน์โหลดและตรวจ canonical torrent → เพิ่มแบบหยุด → recheck → เริ่ม seed
งานที่ค้างในคิวใช้ลำดับเดียวกันโดยเริ่มจากสถานะที่บันทึกไว้
ต้องเปิด `enable_publish`, `enable_seed` และ `enable_resume` เพื่อให้ทำได้ครบทุกขั้นตอน
`max_jobs_per_cycle` นับแต่ละงานหนึ่งครั้ง รวมงานที่พบข้อผิดพลาด ไม่ได้นับแยกตามขั้นตอน
คิวหมุนต่อจากงานที่ลองทำล่าสุด และบันทึกลำดับข้ามการรีสตาร์ตไว้ใน mapping
เมื่อรอบก่อนใช้โควตาครบ รอบถัดไปให้โอกาสค้นหาและทำงานใหม่ 1 งานก่อนกลับมาทำคิวเดิม
งานใหม่ยังรวมอยู่ในโควตาเดิม หากตั้งเพียง 1 งาน จะสลับรอบค้นหางานใหม่กับรอบคิวเดิม
หากไม่พบงานใหม่จะใช้โควตาที่เหลือกับคิวเดิมตามปกติ

ตั้ง `AUTO_UPLOAD.recheck_wait_seconds` เพื่อกำหนดเวลารอ recheck และยืนยันเริ่ม seed ต่อหนึ่งงาน
ค่าเริ่มต้น 120 วินาที รับจำนวนเต็ม 0–1800 และตรวจความคืบหน้าทุก 5 วินาที
ค่า 0 ใช้ตรวจครั้งเดียวแบบเดิม งานที่ยังตรวจไม่เสร็จจะคงเป็น `rechecking`
พร้อม log `recheck_wait` ที่ระบุ `status=wait_timeout` เมื่อหมดเวลารอ
รอบถัดไปตรวจผลต่อโดยไม่ส่งประกาศ เพิ่มทอร์เรนท์ หรือสั่ง recheck ซ้ำ
หาก client รับคำสั่งเริ่ม seed แล้วแต่สถานะยังไม่เปลี่ยน จะอ่านยืนยันต่อภายในช่วงรอเดียวกัน
โดยไม่ส่งคำสั่งเริ่มซ้ำระหว่างการรอ
เวลาของคำขอ API ที่กำลังดำเนินการอาจทำให้เวลารวมเกินช่วงรอที่ตั้งไว้

ยังต้องยืนยันผล recheck, path, manifest และสถานะเริ่ม seed จาก client จริงก่อนบันทึก `seeding`
หากข้อมูลไม่ครบ พบงานซ้ำ หรือผลส่ง/เพิ่มงานไม่แน่นอน จะหยุดตามเงื่อนไขเดิม
การขอ shutdown จะยุติการรอและเก็บสถานะไว้ คำสั่ง CLI `prepare`, `publish` และ `seed`
ยังแยกหน้าที่ตามเดิม โดย `seed` ไม่รอวนตรวจหลายครั้ง

การตรวจซ้ำเลือกเฉพาะแถวผลค้นหา ไม่ใช้ลิงก์รายการล่าสุดในส่วนบัญชี
ใช้ชื่อไฟล์ต้นทางก่อน และเก็บเลขตอนแม้มีป้ายความละเอียดนำหน้า เช่น `4K.S01E10`
พร้อมตรวจชื่อแปลจากเว็บหลัก Dedbit ใช้คำค้นแบบวลีในเครื่องหมายคำพูด
ที่ตรวจการทำงานกับกรณีควบคุมจริงแล้ว กรองรายการที่เป็นคนละชื่อ/คนละตอนที่ระบุชัดเจน
หากพบ piece stream เดียวกันแม้เปลี่ยนชื่อจะถือว่าซ้ำ หาก manifest ของไฟล์ปกติตรงกัน
แต่ padding/piece length ต่างกัน หรือพบไฟล์ใหญ่เดียวกันใน pack จะเป็น `possible_duplicate`
และหยุดรอตรวจสอบ ไม่ยืนยันว่าไม่มีรายการซ้ำ
เมื่อหยุดก่อนส่ง ระบบแยก log เป็น `Duplicate payload already exists` เมื่อพบ payload ซ้ำ,
`Possible duplicate payload requires review` เมื่อยังต้องตรวจสอบ หรือ `Duplicate search incomplete`
เมื่อค้นหาไม่ครบ พร้อมเลขรายการที่พบหรือเหตุผล เช่น เกินจำนวนหน้าที่กำหนด
บันทึกหลักฐานและเวลาตรวจล่าสุดไว้ใน `upload_jobs.<job_id>.duplicate_check` แม้งานยังเป็น `prepared`
ดูผ่าน `show JOB_ID` ได้ การพบงานซ้ำจะไม่ส่งฟอร์มหรือเปลี่ยนเป็น `seeding` อัตโนมัติ

## คำสั่ง

ใช้ Python ที่ติดตั้ง dependencies ของโครงการ เช่น `venv/bin/python` แทน `python`:

```sh
python auto_upload_cli.py status
python auto_upload_cli.py inspect-form --site DEDBIT --category 35
python auto_upload_cli.py inspect-form --site BITSUSE --category 181
python auto_upload_cli.py inspect-form --site TORRENTDD --category 19
python auto_upload_cli.py inspect-form --site TORRENTDD --category 35
python auto_upload_cli.py inspect-source --node 'ชื่อโหนด' --hash 'infohashต้นทาง' --url 'https://bearbit.org/details.php?id=123' --zone normal
python auto_upload_cli.py prepare --node 'ชื่อโหนด' --hash 'infohashต้นทาง' --url 'https://bearbit.org/details.php?id=123' --zone normal
python auto_upload_cli.py prepare --node 'ชื่อโหนด' --hash 'infohashต้นทาง' --url 'https://bearbit.org/details.php?id=123' --zone normal --target-site TORRENTDD
python auto_upload_cli.py show JOB_ID
python auto_upload_cli.py classify JOB_ID
python auto_upload_cli.py repair-category JOB_ID
python auto_upload_cli.py resolve-duplicate JOB_ID
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
ช่องที่ขาดและตัวเลือกของเว็บไว้ให้อ่านด้วย `show` งาน `draft` ที่ข้อมูลยังไม่ครบจะส่งขึ้นเว็บไม่ได้
ถ้าขาดเพียง checkbox ที่รองรับ `enable_publish` จะยืนยันและเปลี่ยนเป็น `prepared` ให้อัตโนมัติ
การรัน `run` จะเตรียมงานที่เพิ่งพบก่อน
แล้วจึงดำเนินขั้นถัดไปในรอบต่อไปตามค่าที่เปิดใช้งาน

`check-job` ตรวจ client, ฟอร์ม และค้นหาซ้ำจริงโดยไม่ส่งฟอร์มหรือเพิ่ม torrent
ผลค้นหาและเวลาตรวจถูกบันทึกใน mapping งานจะยังคง `draft` ถ้าข้อมูลไม่ครบ
การส่งจริงจะตรวจ source และรายการซ้ำใหม่เสมอ จึงไม่ใช้ผลตรวจเก่าเป็นหลักฐานถาวร

`classify` วิเคราะห์หมวดจากข้อมูลที่บันทึกไว้และคืนข้อเสนอโดยไม่แก้ฟอร์ม/สถานะงาน
ค้น MusicBrainz หรือ URL อ้างอิงได้เมื่อระบุ `--musicbrainz`/`--web`
ส่วน `--save` เก็บเฉพาะคำแนะนำ ดูตัวเลือก หลักฐาน และวิธีใช้ไฟล์แค็ตตาล็อกใน
[คู่มือแนะนำหมวดหมู่](METADATA_CLASSIFICATION.md)

เพิ่ม `AUTO_UPLOAD.category_review` เพื่อให้ตรวจและแก้ช่องว่าง/ค่าผิดจากหลักฐานระดับสูง
หลังเตรียมงานและก่อนเผยแพร่ โดยไม่ทับค่าที่ผู้ใช้ระบุ กรณีหลักฐานขัดกันจะหยุดเป็น draft
ส่วน `repair-category JOB_ID` ตรวจฟอร์มสดและแก้เฉพาะงานโดยไม่เผยแพร่
รายละเอียด config และประวัติการแก้ไขอยู่ในคู่มือข้างต้น

ใช้ `configure` เพื่อเติมค่าเฉพาะงานผ่านไฟล์ JSON เช่น:

```json
{
  "fields": {"c35res": "1080i/p"},
  "confirmations": {"ack2": false, "ack4": false}
}
```

กำหนดเฉพาะค่าที่ทราบจริง คำสั่งนี้รับช่องที่เว็บประกาศและ `ack2`/`ack4` ของ Dedbit/Bitsuse
หรือ `cf1`/`cf2` ของ TorrentDD สำหรับงานนั้น และรับ `title` เป็นชื่อประกาศเฉพาะงาน
ตรวจค่ากับตัวเลือกในฟอร์ม ไม่รับการตั้ง `ack3`, token หรือเปลี่ยนเว็บ/หมวดผ่าน override
ค่าเฉพาะงานไม่เปลี่ยนกฎกลางและไม่กระทบงานอื่น ถ้าข้อมูลครบจึงเปลี่ยนเป็น `prepared`
เมื่อปิด `enable_publish` การตรวจ/เตรียมงานจะคงค่า confirmations ตามที่ตั้งไว้
เมื่อเปิด สวิตช์นี้อนุญาตให้ยืนยัน checkbox อัปโหลดที่รองรับ แม้ค่าเดิมจะเป็น `false`
ไม่รวม `ack3` ซึ่งต้องพิสูจน์ด้วยผลค้นหาซ้ำจริงทุกครั้งก่อนส่ง

หากส่งฟอร์มแล้วการเชื่อมต่อขาด ให้ตรวจ listing จริงบนเว็บก่อน แล้วระบุ URL เพื่อกู้สถานะ:

```sh
python auto_upload_cli.py reconcile JOB_ID --url 'https://www.dedbit.com/details.php?id=456'
python auto_upload_cli.py reconcile-seed JOB_ID
python auto_upload_cli.py cancel JOB_ID
```

`reconcile` รับเฉพาะงาน `submitting`/`publication_uncertain` และงาน `verification_failed`
ที่มี listing เดิมแล้ว ตรวจ canonical torrent ก่อนยอมรับ ไม่ส่งฟอร์มซ้ำ
`cancel` รับเฉพาะงาน `draft`/`prepared` ที่ยังไม่ส่งเท่านั้น คิวอัตโนมัติจะไม่เตรียมงานที่ยกเลิกซ้ำ

หาก listing ถูกต้องแต่การตรวจ canonical เคยล้มเหลว หรือ mapping เดิมมี hash นี้แล้ว ให้ใช้:

```sh
python auto_upload_cli.py verify-canonical JOB_ID
python auto_upload_cli.py verify-canonical JOB_ID --url 'https://www.bitsuse.com/bs_details.php?id=687125'
python auto_upload_cli.py reconcile JOB_ID --url 'https://www.bitsuse.com/bs_details.php?id=687125'
```

`verify-canonical` ใช้ listing ที่บันทึกไว้ `--url` ที่ระบุต้องตรงกับ listing นั้นเท่านั้น
คำสั่งทั้งสองแบบดาวน์โหลด canonical ผ่านเซสชันที่ล็อกอินแล้ว ตรวจ payload/tracker
แล้วผูกงานกับ hash เดิมโดยไม่ POST ไม่เพิ่ม torrent ไม่ recheck ไม่ resume และไม่ลบไฟล์
การยอมรับ mapping ที่มีอยู่จะเกิดขึ้นก็ต่อเมื่อ site_record มี hash ตรงกันและมี
`binding_evidence`, แถว torrent มี site/zone/listing/infohash ตรงกันและอยู่ใน group เดียวกัน
หรือยังไม่ได้ผูก group และไม่มีงานอื่นถือ hash/listing นั้นอยู่ H&R/Quest, manual hold
และ `node_locations` เดิมจะถูกเก็บไว้ ถ้าหลักฐานไม่ตรงจะปฏิเสธและคงสถานะเดิมไว้

หาก torrent ปลายทางมีอยู่และกำลัง seed อยู่แล้ว (ไม่ควรหยุด/recheck) ให้ใช้:

```sh
python auto_upload_cli.py adopt-seeding JOB_ID
```

คำสั่งนี้ตรวจ source/canonical artifacts, save path, ความครบของไฟล์ และสถานะ
`uploading`/`stalledUP`/`forcedUP` จาก client สด แล้วเปลี่ยนงาน `verified` เป็น `seeding`
โดยไม่เรียก add/recheck/resume/delete/stop หลักฐานเดิมใน `canonical_verification`
และ `node_locations` จะถูกเก็บไว้ พร้อมเพิ่ม `seed_recovery.method: existing_active_target`
ถ้า torrent ไม่ได้ seed อยู่จะปฏิเสธและให้ใช้ `seed` ตามขั้นตอนปกติแทน

### ผูกรายการซ้ำที่ seed อยู่แล้ว

ถ้างานเป็น `draft`/`prepared` และพบ payload ซ้ำ แต่ torrent ของรายการปลายทาง
มีอยู่บน client ต้นทางและกำลัง seed อยู่แล้ว ใช้:

```sh
python auto_upload_cli.py resolve-duplicate JOB_ID
python auto_upload_cli.py resolve-duplicate JOB_ID --url 'https://www.dedbit.com/details.php?id=123'
```

ค่าเริ่มต้นใช้ URL จากผล `duplicate_check` ที่เป็น `duplicate` และ `complete: true`
ส่วน `--url` ระบุรายการปลายทางให้ตรวจโดยตรงได้ ไม่ใช้ผลซ้ำเก่าเป็นหลักฐานยืนยันไฟล์
คำสั่งจะอ่าน canonical ใหม่จากเว็บที่ล็อกอิน ตรวจ tracker แบบ private และตรวจ
infohash, layout/pieces, client, save path และความครบของไฟล์ทั้งต้นทาง/ปลายทาง
ปลายทางต้องกำลัง seed อยู่ (`uploading`, `stalledUP`, `forcedUP` ของ qBittorrent)
ไม่รับกรณีเพียงชื่อคล้าย ไฟล์เปลี่ยนชื่อ/ตำแหน่ง หรือ hash ที่ผูกกับงานอื่น

เมื่อตรวจผ่านจะกู้ source/canonical artifact ในโฟลเดอร์ปัจจุบัน ผูก `site_records`,
`content_groups`, `site_listings`, `node_locations` และปรับงานเป็น `seeding`
พร้อม `duplicate_resolution.method: existing_destination_torrent`
ข้อมูลนี้บอกว่าใช้รายการที่มีอยู่แล้ว ไม่ใช่หลักฐานว่าโปรแกรมเพิ่งอัปโหลดรายการนั้น
คง H&R/Quest/manual hold เดิม และตั้งภาระรายการใหม่เป็น `unknown` จนกว่าจะตรวจได้
ไม่ส่งฟอร์ม ไม่เพิ่ม torrent ไม่ recheck ไม่ resume ไม่ลบไฟล์ การเรียกซ้ำตรวจข้อมูลสดอีกครั้ง
แต่ไม่เพิ่ม mapping ซ้ำ

หาก source artifact อยู่พาธเครื่องเดิม จะลอง export จาก qBittorrent แล้วตรวจ
hash/payload/path/ไฟล์ครบก่อนใช้ client อื่นต้องมี artifact ที่อ่านได้
ปลายทางที่ยังไม่อยู่บน client หรือหยุดอยู่ต้องจัดการผ่าน Cross-Seed ตามขั้นตอนปกติ
งาน `submitting`/`publication_uncertain` ยังคงใช้ `reconcile` และงาน repack ต้องตรวจแยก
คำสั่งนี้ต้องเปิด `AUTO_UPLOAD.enable` และเปิด node/site/zone ที่เกี่ยวข้อง
ไม่จำเป็นต้องเปิด `enable_publish`, `enable_seed` หรือ `enable_resume` เพราะไม่มีการทำขั้นตอนเหล่านั้น
หากต้องแก้กฎแล้วเตรียมใหม่ให้เรียก `prepare` โดยตรงเพื่อยืนยันต้นทางใหม่ งาน `adding`/`seed_uncertain`
จะไม่เพิ่มซ้ำ ใช้ `reconcile-seed` เพื่อตรวจงานที่มีอยู่จริงและสั่ง recheck ใหม่โดยไม่เพิ่มงาน
หากไม่มี hash ปลายทางใน client จะคงสถานะเดิมไว้ให้ตรวจสอบ

เมื่อ `verified` พบ hash ปลายทางอยู่ก่อนแล้ว หรือคิวพบ `seed_uncertain` ในรอบถัดไป
ระบบตรวจ source client เดิม, completion ของต้นทาง, canonical hash/payload, save path
และรายการชื่อ/ขนาดไฟล์ปลายทางจาก client สดก่อนใช้รายการเดิม
การกู้อัตโนมัติรับเฉพาะรายการที่หยุดอยู่ โดยยังต้องเปิด `enable_seed` และกฎของงานนั้น
จากนั้นบันทึก `adding` ก่อนสั่ง recheck และเปลี่ยนเป็น `rechecking` เพื่อติดตามผลตามปกติ
ไม่มีการส่งประกาศหรือเพิ่มทอร์เรนท์ซ้ำ ผลตรวจอยู่ใน `seed_recovery` ของคำสั่ง `show`
ถ้าไม่พบรายการปลายทาง ข้อมูลไม่ตรง หรือรายการกำลังทำงานอยู่ จะคงสถานะไว้ให้ตรวจสอบ
งาน `adding` ยังต้องใช้คำสั่งกู้โดยตรง ส่วน `submitting`/`publication_uncertain` ไม่ส่งซ้ำอัตโนมัติ

เลือกคำสั่งตามสถานะของคิว ไม่ใช่ตามสถานะที่เห็นใน WebUI เพียงอย่างเดียว:

| สถานะคิว | คำสั่ง |
|---|---|
| `verified` | `seed JOB_ID` เพื่อเพิ่มแบบหยุดและขอ recheck |
| `adding` / `seed_uncertain` | `reconcile-seed JOB_ID` เพื่อตรวจ hash ที่มีอยู่และขอ recheck โดยไม่เพิ่มซ้ำ |
| `rechecking` | `seed JOB_ID` เพื่อตรวจความคืบหน้า และเริ่ม seed เฉพาะเมื่อตรวจครบและเปิด `enable_resume` |
| `seeding` | ไม่ต้อง reconcile ขั้นเพิ่มงานอีก |

qBittorrent adapter รองรับทั้งผลเพิ่มงานแบบ `Ok.` เดิมและ JSON batch summary
ของ WebAPI 2.14 ขึ้นไป สำหรับไฟล์ที่ส่งครั้งละหนึ่งรายการ JSON ต้องยืนยันสำเร็จ
หนึ่งรายการ ไม่มี pending/failure และ hash ที่เพิ่มต้องตรงกับ canonical torrent
ผลไม่ครบหรือยัง pending จะยังคงเข้าสู่ `seed_uncertain` เพื่อกู้จาก inventory
โดยไม่ส่ง add ซ้ำ คำสั่ง recheck/start รองรับ HTTP 204 ของ client รุ่นใหม่ด้วย

`publication_uncertain` หมายถึงเริ่มขั้นตอนส่งแล้วแต่ยังยืนยันผลไม่ได้ ไม่ได้ยืนยันว่าเว็บสร้างรายการแล้ว
และการค้นหาปัจจุบันที่ไม่พบรายการก็ไม่ใช่หลักฐานว่าปลอดภัยที่จะส่งซ้ำ
ระบบบันทึก `submission_started_at` ก่อนเรียกส่ง และเมื่อเกิดปัญหาจะเก็บ
`last_publication_failure` ซึ่งมี `stage`, `page_path`, `error_type`, `checked_at`
ขั้นตอนแบ่งเป็น `prepare_submit`, `submit`, `await_result` และ `resolve_listing`
ดูผ่าน `show JOB_ID`, log ของคิว และการแจ้งเตือนได้ ไม่บันทึกข้อความ exception ดิบ,
query string, cookies หรือเนื้อหาหน้าเว็บ ข้อมูลนี้ใช้กับความล้มเหลวที่เกิดหลังอัปเดตโค้ดเท่านั้น
งานเก่าที่มีเพียง `RuntimeError` จะไม่สามารถย้อนคืนรายละเอียดเหตุการณ์ที่ไม่ได้บันทึกไว้
เมื่อพบรายการจริงและยืนยันแล้ว ให้ใช้ `reconcile` ตาม URL ของรายการ ไม่เปลี่ยนกลับเป็น `prepared` เพื่อส่งซ้ำ

Bitsuse ที่ตอบกลับด้วย `/bs_browse.php` หลัง POST จะถูกตามหาคำนำหน้าด้วยการค้นหาแบบอ่านอย่างเดียว
ทั้งในรอบส่งและรอบคิวถัดไป (สูงสุด 6 รอบต่องาน) เพื่อรอรายการที่ยังไม่ปรากฏในผลค้นทันที
หากพบรายการที่ตรง payload/announce ระบบจะบันทึก listing แล้วตรวจ torrent ต้นฉบับต่ออัตโนมัติ
ไม่มีการ POST ซ้ำและไม่ปลดสถานะ `publication_uncertain` ด้วยผลค้นหาที่ว่าง
ผลตรวจแต่ละรอบเก็บใน `publication_check` ดูได้จาก `show JOB_ID` หากหมดรอบให้ตรวจ My Uploads แล้วใช้
`retry-publication` ตามขั้นตอนด้านล่างเท่านั้น

### ยืนยันว่าไม่มีประกาศ แล้วอนุญาตส่งใหม่

หากตรวจ **My Uploads / รายการที่ฉันอัปโหลด** ของบัญชีปลายทางแล้วว่าไม่มีงานนี้จริง
ใช้คำสั่งต่อไปนี้กับงาน `publication_uncertain` ที่ยังไม่มี listing/hash ปลายทางบันทึกไว้:

```sh
python auto_upload_cli.py retry-publication JOB_ID --confirm-not-published
python auto_upload_cli.py show JOB_ID
```

ต้องอัปเดตโค้ดและรีสตาร์ทบอททุก instance ให้เป็นรุ่นที่รองรับคำสั่งนี้ก่อน
`--confirm-not-published` เป็นการยืนยันผลตรวจของผู้ใช้ ไม่ใช่การสรุปจากผลค้นหาว่าง
ไม่รับสถานะ `submitting` เพราะอาจยังมี process กำลังส่งอยู่

คำสั่งตรวจ source ที่โหนดเดิม/path เดิม ไฟล์ครบ payload/variant เดิม ฟอร์มปลายทางสด
และค้นหาซ้ำใหม่ หากพบงานซ้ำ, อาจซ้ำ, ค้นหาไม่ครบ หรือ API ผิดพลาด จะไม่อนุญาตส่งใหม่
หลักฐานค้นหาที่ได้บันทึกใน `publication_retry_check`; ผลขัดข้องที่ยังไม่มีหลักฐานไม่เปลี่ยนสถานะงาน

เมื่อตรวจผ่าน จะเก็บข้อมูลการส่งครั้งเดิมใน `publication_history`
และกลับเป็น `prepared` หรือ `draft` หากฟอร์มยังขาดข้อมูล โดยคงการป้องกัน source จาก Cleaner
งานมี `publication_retry.manual_publish_required: true` จึงไม่ถูกคิวอัตโนมัติส่ง แม้เปิด `enable_publish`
ดู/แก้ข้อมูลด้วย `show`, `check-job`, `configure` ได้ตามเดิม แล้วสั่งส่งโดยตรงเมื่อพร้อม:

```sh
python auto_upload_cli.py publish JOB_ID
```

`publish` ยังต้องเปิด `AUTO_UPLOAD.enable`, `enable_publish` และกฎหมวดเดิม
ตรวจต้นทาง ฟอร์ม และงานซ้ำใหม่อีกครั้งก่อน POST และใช้สิทธิ์ส่งใหม่นี้เมื่อเริ่มส่งจริงเท่านั้น
ถ้าผลส่งครั้งใหม่ยังไม่ชัดเจน จะกลับ `publication_uncertain` และไม่ส่งซ้ำอัตโนมัติ
คำสั่ง `retry-publication` เองไม่ส่งฟอร์มหรือเพิ่ม/เริ่ม torrent บน seedbox

### ปิดงานเก่าที่ต้นทางหายจาก client แล้ว

หากตรวจปลายทางแบบอ่านอย่างเดียวแล้วแน่ใจว่าไม่มีรายการ แต่ torrent ต้นทางได้หายไปจาก
client แล้ว `retry-publication` จะตรวจ source สดไม่ผ่าน ให้ใช้คำสั่งปิดงานแบบสมบูรณ์แทน:

```sh
python auto_upload_cli.py close-uncertain JOB_ID --confirm-not-published
python auto_upload_cli.py show JOB_ID
```

คำสั่งนี้ตรวจเฉพาะ artifact ที่เก็บไว้ (hash/layout ของ source, prepared และ submission ต้องตรงกัน)
แล้วเปลี่ยน `publication_uncertain` เป็น `cancelled` โดยไม่ส่ง POST และไม่เรียก add/recheck/resume/stop/delete
หลักฐานการตรวจและการยืนยันของผู้ใช้เก็บใน `publication_history` และ `publication_resolution`
งานที่ถูกปิดจะไม่ถูกคิวนำไปส่งซ้ำ แต่ยังดูย้อนหลังได้ด้วย `show JOB_ID`
ใช้เฉพาะงานที่ไม่มี listing/hash ปลายทาง และห้ามใช้กับ `submitting` ที่อาจยังมี process กำลังส่งอยู่

## การแจ้งเตือน

Auto Upload ส่งผ่าน `send_notify` เดียวกับระบบหลัก ใช้การตั้งค่า LINE, Telegram และ Discord เดิม
ไม่ต้องเพิ่ม token หรือผู้รับใน `AUTO_UPLOAD` อีกชุด เปิดช่องทางที่ต้องการผ่าน
`LINE_CONFIG.enable` หรือ `TELEGRAM_CONFIG.notify_enable` / `DISCORD_CONFIG.notify_enable`
พร้อมข้อมูลบัญชีและผู้รับตามคู่มือหลัก

แจ้งเมื่อเตรียมงานเป็น `draft`/`prepared`, อัปโหลดและตรวจไฟล์สำเร็จ, เริ่ม recheck,
เริ่ม seed หรือมีปัญหาที่ต้องตรวจสอบ เช่น พบงานซ้ำ ตรวจไฟล์ไม่ผ่าน หรือผลการส่งไม่ชัดเจน
ข้อความมีชื่อเรื่อง เส้นทางเว็บ/โซน โหนด Job ID และลิงก์รายการปลายทางเมื่อยืนยันได้
หากทำหลายขั้นสำเร็จใน cycle เดียว จะรวมเป็นข้อความสถานะล่าสุดของงานนั้น
การข้ามต้นทางที่อยู่คนละโซนหรือยังไม่ผ่าน preflight และยังไม่มี job จะแสดงเฉพาะ log

บันทึกคิวแจ้งเตือนและผลส่งล่าสุดที่ `upload_jobs.<job_id>.notifications` ดูผ่าน `show JOB_ID` ได้
ไม่ส่งข้อความสถานะ/เหตุผลเดิมซ้ำเมื่อบันทึกส่งสำเร็จแล้ว แม้รีสตาร์ตบอท
หากส่งไม่สำเร็จทุกช่องทางจะเก็บข้อความล่าสุดและลองส่งใหม่ใน cycle ถัดไป
การส่งสำเร็จอย่างน้อยหนึ่งช่องทางถือว่าแจ้งแล้ว หากเกิดปัญหาที่ช่องทางอื่นจะไม่ส่งซ้ำไปยังช่องทางที่สำเร็จ
ผลการส่งแจ้งเตือนไม่เปลี่ยนสถานะ upload และไม่สั่งส่งฟอร์มหรือเพิ่มงานใน seedbox ซ้ำ
งาน `seeding` เดิมที่ไม่มีข้อความรอส่งจะไม่แจ้งย้อนหลังเมื่ออัปเดตโค้ด

เมื่อยังมีงานค้าง ระบบส่งสรุปจำนวนงาน สถานะ และเงื่อนไขที่ยังติดหลังจบรอบ
ค่าเริ่มต้นไม่เกินชั่วโมงละครั้ง ปรับด้วย `AUTO_UPLOAD.summary_interval_seconds`
หรือใช้ 0 เพื่อปิดเฉพาะสรุป ข้อความสถานะใหม่ของแต่ละงานยังส่งตามเดิม
สรุปอิงรอบ Auto Upload จริง ไม่ใช่ตัวตั้งเวลารายชั่วโมงแยกต่างหาก
mapping เก็บลำดับคิวที่ `auto_upload_runtime.scheduler` และคิวสรุป/เวลาส่งล่าสุดที่
`auto_upload_runtime.notifications` เพื่อไม่ส่งถี่ขึ้นหลังรีสตาร์ต
log `notice_queued` และ `notice_result` แสดงผลส่งโดยไม่บันทึกเนื้อหาหรือข้อมูลบัญชี

ครอบคลุมบอทหลัก, CLI `run` และคำสั่ง `prepare`/`publish`/`seed`/`reconcile`/`reconcile-seed`/`retry-publication`
คำสั่งตรวจสอบ เช่น `status`, `show`, `inspect-source` และ `check-job` ไม่ส่งแจ้งเตือน

## ข้อมูลใน mapping และ Cleaner

เพิ่ม `upload_jobs` และ `content_groups` ลง schema 2 โดยคงข้อมูลเดิมไว้
`content_group_id` คำนวณจาก v1 layout และ piece hashes ไม่จัดกลุ่มด้วยชื่อหรือขนาดรวมอย่างเดียว
เมื่อยืนยัน canonical torrent แล้ว จะเพิ่ม variant ใน `torrents`, เชื่อม `site_records`
และเก็บภาระ H&R/Quest ของปลายทางเป็น `unknown` จนตัวอ่านเว็บจะยืนยันจริง

งานระหว่างดำเนินการจะป้องกันทั้ง hash ต้นทางและปลายทางจาก Cleaner แม้งานเก่าจะไม่มี
H&R แล้ว หลังยืนยันและเริ่ม seed จะปล่อย hold ของคิว ส่วน guard ไฟล์ร่วมและภาระ H&R/
Quest ยังคงทำงานตามปกติ การค้างในสถานะไม่แน่นอนจะคง hold ไว้เพื่อรอตรวจสอบ

งาน BITSUSE ที่เผยแพร่แล้วตรวจ peer ไม่ได้จากหน้า BITSUSE ที่ซ่อน endpoint ระบบจึงอ่าน
details/ajaxpeers ของ DEDBIT ด้วย `torrent_id` เดียวกัน แล้วเทียบ canonical hash กับ
`target_hash` ของงานก่อนยอมรับเสมอ `retention_check` ยังผูกกับงานเดิมและมี
`verification_site='DEDBIT'` เพื่อให้ audit ได้ชัด ผู้ใช้/peer ที่เป็นของบัญชีเราถูกหออกก่อนนับ
และจำนวน seed ที่หน้าเว็บแจ้งอาจรวมหรือไม่รวม connection ของเราได้

ไฟล์ `.torrent` มี passkey จึงเก็บใน `upload_artifacts/` ที่ gitignore และใช้สิทธิ์ `0600`
ไม่บันทึก CSRF, cookies หรือ announce ที่มี passkey ลง mapping ห้ามนำโฟลเดอร์นี้ขึ้น Git
ไฟล์ทดสอบยังเก็บเฉพาะเครื่องตามแนวทางของโครงการ

### ลดการอ่านต้นทางซ้ำในรอบเดียว

ระบบใช้ metadata และ metainfo ต้นทางที่อ่านสำเร็จแล้วร่วมกันเมื่อหลายโหนดมี
hash, listing ID และ zone เดียวกัน โดยยังตรวจ completion และรายการไฟล์สดของแต่ละโหนด
ก่อนเตรียมงานปลายทาง และไม่เก็บ cache ข้ามรอบ
ผลที่ยืนยันว่า zone ไม่ตรงหรือยังไม่มี category rule จะไม่อ่านเว็บซ้ำในรอบเดียวกัน
ส่วนข้อผิดพลาดเครือข่ายและการล็อกอินยังลองใหม่ได้ตามปกติ
เมื่อขอ shutdown จะไม่เริ่มงานหรือขั้นตอน publish/seed ถัดไป และรอขั้นตอนที่เริ่มแล้วให้จบ


### ขนาดไฟล์ metainfo เกินข้อจำกัดปลายทาง

ตัวอ่าน Dedbit/Bitsuse อ่าน `MAX_FILE_SIZE` จากฟอร์มปัจจุบัน และตรวจขนาด
variant `.torrent` ก่อนเตรียมงาน ตรวจงาน อนุญาต retry และกรอกไฟล์ส่งฟอร์ม
ขนาดที่เท่าข้อจำกัดยังผ่านได้ แต่ขนาดที่เกินจะหยุดด้วย
`Torrent metainfo exceeds destination limit` โดยไม่ส่ง POST
ข้อจำกัดนี้เป็นขนาดไฟล์ `.torrent` ไม่ใช่ขนาดข้อมูลที่จะ seed

หากตาราง piece hashes และรายชื่อไฟล์ทำให้ metainfo เกินข้อจำกัด ต้องสร้าง
metainfo ใหม่ด้วย piece size ใหญ่ขึ้นจากไฟล์จริง การลบ metadata ส่วนบน
หรือแก้ค่า `piece length` อย่างเดียวใช้แทนการ rehash ไม่ได้
คิวตรวจความสามารถของ client อัตโนมัติ เฉพาะเมื่อ variant เดิมเกิน
`MAX_FILE_SIZE` จริง และเลือกตัวสร้างที่รองรับบนโหนดต้นทาง:

| Client / ผู้ให้บริการ | ช่องทางสร้างใหม่ |
| --- | --- |
| qBittorrent | Torrent Creator WebAPI; ตรวจ endpoint จริง ไม่ตัดสินจากรุ่นอย่างเดียว |
| rTorrent / Swizzin | ปลั๊กอิน Create และ Task ของ ruTorrent บน origin เดียวกับ RPC |
| Deluge 2.2+ | `core.create_torrent` และ `core.get_path_size` ผ่าน Web/daemon RPC |
| OnlyOne | SSO maker เมื่อ native API ไม่มี โดยใช้ downloads เริ่มต้น |

ruTorrent ตรวจ `/rutorrent` อัตโนมัติ หรือระบุ `rutorrent_url` ใน node
สำหรับพาธอื่นได้ ต้องเป็น origin เดียวกับ RPC เพื่อใช้บัญชีเดิม
Deluge 1.3–2.1 ไม่รองรับการรับผลสร้างกลับทาง RPC จึงหยุดให้ตรวจเอง
ระบบไม่ส่งคำสั่ง shell ผ่าน RPC และไม่ติดตั้งเครื่องมือบนเซิร์ฟเวอร์

ระบบอ่านผลสร้างที่เสร็จอยู่แล้วและตรงกับโฟลเดอร์ต้นทางก่อน หากต้องเริ่มงานใหม่
จะบันทึกคำขอก่อน POST แล้วให้รอบถัดไปอ่านสถานะ ไม่ส่ง POST ซ้ำเมื่อผลยังไม่ชัดเจน
ตัวสร้าง native เลือก piece size ที่ใหญ่ขึ้นเป็นกำลังสอง สูงสุด 64 MiB
จากขนาด hash table ที่คาดว่าจะพอดี ส่วน OnlyOne เลือกขนาดให้เอง
ทุกช่องทางต้องตรวจขนาด variant จริงอีกครั้ง หากยังเกินจะหยุด
ไม่ปรับค่าข้อจำกัดของเว็บปลายทาง และไม่ fallback เปลี่ยน provider เมื่อส่งคำขอแล้ว

qBittorrent/ruTorrent จับคู่ task ด้วย marker เฉพาะคำขอและพาธ/piece size
จึงติดตามผลได้แม้ response ของ POST หาย โดยไม่สั่งสร้างซ้ำ
Deluge 2.2 ส่งผลกลับพร้อมเขียนไฟล์ชื่อเฉพาะไว้ข้างต้นทาง โดยปิด `add_to_session`
หาก RPC timeout หรือโปรเซสหยุดก่อนเก็บผลในเครื่อง ต้องตรวจไฟล์บนเซิร์ฟเวอร์เอง
ระบบคงคำขอไว้และไม่สั่งสร้างทับซ้ำ หรือลบไฟล์/task ของ client อัตโนมัติ

ผลใหม่ต้องเป็น v1 มี root และรายชื่อไฟล์/ขนาดตรงกับต้นทางทุกไฟล์ และมี piece
size ใหญ่ขึ้น ขั้นนี้ยังไม่รองรับ padding หรือ symlink
ข้อจำกัด downloads เริ่มต้นใช้เฉพาะ OnlyOne maker
ระบบเก็บ artifact และ signature ใหม่แยกจาก source เดิม โดย signature ของการส่ง
แต่ละครั้งยังถูกผูกกับ canonical torrent ของครั้งนั้น ตรวจ recheck บน client
ก่อนเริ่ม seed เสมอ การตรวจรายชื่อและขนาดไฟล์ไม่ใช่หลักฐานว่า byte ตรงกัน

ตรวจหรือเริ่ม repack โดยไม่เผยแพร่รายการได้ด้วย:

```bash
python auto_upload_cli.py repack JOB_ID --max-bytes 1000000
python auto_upload_cli.py repack-capabilities --node ReadyIDC
```

ใช้ค่าจากฟอร์มปลายทางจริง หาก maker กำลังทำงาน ให้เรียกคำสั่งเดิมภายหลังเพื่อ
อ่านสถานะและตรวจผล คิว publish ตรวจข้อจำกัดสดจากฟอร์มและใช้เส้นทางเดียวกัน
การ repack ไม่ปลด `publication_uncertain` และไม่อนุญาตส่งซ้ำโดยอัตโนมัติ
งานที่ยังมีคำขอ maker ไม่ได้ข้อสรุปจะยกเลิกเพื่อปล่อยการป้องกันต้นทางไม่ได้

`repack-capabilities` ตรวจ API แบบอ่านอย่างเดียว ไม่สร้าง task หรือเผยแพร่งาน
อ้างอิง API: [qBittorrent Creator](https://github.com/qbittorrent/qBittorrent/blob/master/src/webui/api/torrentcreatorcontroller.cpp),
[ruTorrent Create](https://github.com/Novik/ruTorrent/blob/master/plugins/create/action.php),
[Deluge 2.2 core](https://github.com/deluge-torrent/deluge/blob/deluge-2.2.0/deluge/core/core.py)

งาน `publication_uncertain` ยังคงต้องตรวจผลเผยแพร่ก่อนเปลี่ยนสถานะหรือส่งซ้ำ
การพบไฟล์ใหญ่เกินข้อจำกัดไม่ใช่หลักฐานว่าการส่งครั้งก่อนปราศจากผลข้างเคียง

งานที่เผยแพร่แล้วต้องผ่านเงื่อนไข Cleaner เพิ่มก่อนเลิก seed:
มีผู้ปล่อยอื่นที่ยืนยันบัญชีได้อย่างน้อย 1 คน และยอดโหลดเสร็จจากเว็บอย่างน้อย 1 ครั้ง
หากอ่านตารางเพียร์ไม่ได้ แยกบัญชีเราไม่ได้ หรือผลตรวจเกิน 5 นาที จะยังเก็บงานไว้
ตรวจผล `retention_check` ด้วยคำสั่ง `show JOB_ID` และอ่านรายละเอียดใน
[เงื่อนไข Cleaner สำหรับ Auto Upload](TORRENT_MAPPING.md#งานที่เผยแพร่ผ่าน-auto-upload)


### Log การทำงาน

Log Auto Upload ใช้ prefix `[Auto Upload]` พร้อมเวลาไทย (UTC+07:00) และ JSON
หนึ่งบรรทัดต่อเหตุการณ์ โปรแกรมหลักเขียนรวมใน `script_run.log` ตามสคริปต์เริ่มระบบ
ส่วน CLI แสดงทาง stdout เหมือนเดิม ทุกบรรทัด flush ทันที

| event | ความหมาย |
|---|---|
| `cycle_wait` | รอตรวจ node หรือรอรอบค้นหา/Cross-Seed ก่อนเข้าคิวอัปโหลด |
| `cycle_start` / `queue` | เริ่มรอบ สวิตช์ จำนวนงานสูงสุด และจำนวนงานแต่ละสถานะ |
| `job_start` / `stage` | งานและขั้นตอนปัจจุบัน เช่น `check_source`, `duplicate_check`, `fill_form`, `submit_form` |
| `job_skip` | เหตุผลที่ข้าม เช่น `reconciliation_required`, `source_node_unavailable`, `manual_publish_required`, `seed_disabled` |
| `duplicate_result` | ผลค้นหาซ้ำ ความครบถ้วน และจำนวนหน้าที่ตรวจ |
| `repack_start` / `repack_status` / `repack_ready` | ตัวสร้างที่เลือก, piece size และขนาด metainfo ใหม่ที่ผ่านการตรวจ |
| `state_change` / `recheck_wait` | สถานะหลังส่ง ตรวจ canonical เพิ่มแบบหยุด และผล recheck ก่อน seed |
| `job_error` / `cycle_error` | งานหรือรอบทำงานขัดข้อง พร้อมชนิดข้อผิดพลาด |
| `cycle_limit` / `cycle_summary` | ถึงจำนวนงานต่อรอบ และสรุปจำนวนที่ทำ/ข้าม/ยังไม่ได้พิจารณา พร้อมเวลาที่ใช้ |

`job` และ hash แสดง 12 ตัวแรก ใช้เทียบกับคำสั่ง `status`/`show` ได้
จำนวน `processed` นับงานที่พยายามทำ รวมงานที่เกิดข้อผิดพลาด ซึ่งยังใช้
`max_jobs_per_cycle` ตามพฤติกรรมเดิม งาน seeding/cancelled สรุปรวมในคิวและ
จำนวนข้าม เพื่อไม่พิมพ์ซ้ำทุกงาน `cycle_summary.status=completed` หมายถึง
ประมวลผลรอบจบ ไม่ได้หมายความว่าทุกงานสำเร็จ ให้ดู `job_error` ประกอบ

เลือกดูเฉพาะ Auto Upload แบบต่อเนื่องได้ด้วย:

```bash
tail -f script_run.log | grep --line-buffered '\[Auto Upload\]'
```

Logger รับเฉพาะฟิลด์ความคืบหน้าที่กำหนดไว้ ไม่พิมพ์ config, HTML, artifact path,
exception body ทั่วไป หรือ announce URL และกรอง URL/ค่าความลับจากข้อความ
ข้อผิดพลาดที่รองรับ การเพิ่ม log ไม่เปลี่ยนเงื่อนไขเผยแพร่หรือ retry งาน
