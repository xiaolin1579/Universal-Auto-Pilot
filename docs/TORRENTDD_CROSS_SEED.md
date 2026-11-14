# Cross-Seed สำหรับ TorrentDD

TorrentDD ใช้ขั้นค้นหาและ preflight ร่วมกับระบบ Cross-Seed เดิม โดยเพิ่มตัวอ่าน
ใน `torrentdd_cross_seed.py` สำหรับค้นหารายการที่มีอยู่บนเว็บไซต์แล้ว
การอัปโหลดสร้างรายการใหม่บน TorrentDD ยังไม่อยู่ในเส้นทาง Auto Upload

## โซนและคำค้น

เปิดเว็บไซต์ TorrentDD ใน `SITE` และกำหนด `target_urls` ของเว็บไซต์นั้นตามโซน
ที่จะค้นหา ตัวอย่างเฉพาะส่วนโซน:

```json
{
  "target_urls": [
    {
      "name": "โซนปกติ",
      "url": "https://torrentdd.com/browse.php",
      "enable": true
    },
    {
      "name": "โซนพิเศษ",
      "url": "https://torrentdd.com/browse18.php",
      "enable": false
    }
  ]
}
```

ให้นำไปแก้เฉพาะ `target_urls` ของรายการ TorrentDD โดยคงบัญชีและเว็บไซต์อื่นไว้
ระบบค้นหาเฉพาะโซนที่เปิดอยู่ ไม่เปิดโซนพิเศษหรือสร้างโซนสำรองเพิ่มเมื่อไม่มีรายการโซน
เว็บไซต์จริงเปลี่ยนจาก `torrentdd.com` ไป `www.torrentdd.com` ผ่าน HTTPS
ตัวอ่านรองรับสอง hostname นี้ โดยไม่ใช้ alias ของโดเมนอื่น

ตัวสร้าง URL อิง controls ของ TorrentDD ที่ตรวจจากหน้าเว็บ:

| ค่าใน search params | Query ที่ส่ง |
|---|---|
| `search_keyword` | `search` |
| `selected_cats` | `cate` โดยคั่นเลขหมวดด้วย comma |
| `freeload: true` | `freeload=yes` |

ไม่มีการส่ง `incldead`, `searchin`, `sortby` หรือ `cNN=1` ตามรูปแบบของ tracker อื่น
หมวดของโซนปกติคือ `1`–`33` และ `40` ส่วนโซนพิเศษคือ `34`–`38`
หากตั้งหมวดผิดโซนหรือไม่รู้จัก จะไม่สร้างคำค้นที่ขยายกลับไปทุกหมวด
filters ใน URL เช่น `?cate=1,40&freeload=yes` ยังคงถูกส่งต่อ
และ `search_params` ของโซน/เป้าหมายสามารถกำหนดทับตามกติกาเดิม

คำค้นสำรองยังใช้ `cross_seed_search.search_keywords()` โดยคงเลขตอนและรหัสไฟล์
ค่าเริ่มต้นค้นหาไม่เกินสามคำต่อ source/zone ปรับผ่าน `SETTING.CROSS_SEED_MAX_QUERIES`
ผลค้นหาเป็นเพียง candidate ยังไม่ถือว่าเป็นไฟล์เดียวกัน

## การตรวจรายการและไฟล์

1. ตรวจว่าหน้าค้นหาที่โหลดตรงกับ URL คำค้นและโซนที่ขอ และยังล็อกอินอยู่
2. อ่านเฉพาะแถวในตารางผลค้นหาของ TorrentDD แยกตารางโซนปกติ/พิเศษ
   ไม่ใช้รายการใน sidebar หรือ `userdetails.php` เป็น torrent
3. ผูก ID ของ `details.php` กับปุ่มดาวน์โหลด `download.php/ID/ชื่อ.torrent`
   ข้ามปุ่มอื่น และไม่ยอมรับ download ที่เป็นคนละ ID หรืออยู่นอกเว็บไซต์
4. ดาวน์โหลด metainfo ผ่าน session ของเว็บไซต์ โดยใช้ cookies ที่ตรงกับ URL
   และตรวจ bencode/manifest จริง ไฟล์ที่ถูกต้องไม่จำเป็นต้องเริ่มด้วย `d8:announce`
5. อ่าน File List จาก modal `#filelistModal` ในหน้ารายละเอียดของ ID เดียวกัน
   ตรวจหัวตาราง `ชื่อไฟล์`/`ขนาด` และเทียบกับ manifest ของ `.torrent`
6. ใช้ preflight เดิมเทียบชื่อ/path/ขนาดกับ manifest ของ source client สด
   แล้วส่งผลให้ชั้น audit/execution เดิมพิจารณาต่อ

ขนาดที่แสดงบนเว็บเป็นค่าปัดเศษ ตัวตรวจจะเทียบกับจำนวน byte ใน metainfo
ทั้งหน่วย decimal และ binary เมื่อป้ายหน่วยกำกวม การเทียบนี้ยังไม่ยืนยันข้อมูลทุก piece
งานคนละ infohash ต้องผ่าน recheck ก่อนเริ่ม seed

หน้า login, redirect คนละ ID, File List ที่ขาดหรืออ่านไม่ได้ และข้อมูลผิดรูปแบบ
จะไม่ถูกนับว่าตรวจผ่าน กรณีซ่อนรายการใช้ fallback ได้เฉพาะข้อความ `ไม่เปิดเผย`
ที่อยู่ใน modal ของหน้าที่ล็อกอินและผูก ID ถูกต้อง และยังต้องผ่านการเทียบ manifest
ของ source กับ candidate โดยเคร่งครัด

ตัวดาวน์โหลดจำกัด metainfo ที่ 16 MiB ตรวจ HTTPS และ redirect ทุกขั้น
ไม่ส่ง cookies ไปเว็บไซต์อื่น และไม่แสดง URL ดาวน์โหลด/ข้อมูล session ในข้อความ error

## การเพิ่มงานจริง

สวิตช์ execution ยังคงแยกจากการค้นหาและเริ่มต้นปิด:

```json
{
  "CROSS_SEED_EXECUTION": {
    "enable_tracker_add": false,
    "enable_new_hash_add": false
  }
}
```

- hash เดียวกัน: การเพิ่ม tracker ต้องผ่าน guard และเปิด `enable_tracker_add`
- hash ต่างกัน: ต้องเปิด `enable_new_hash_add` และผ่านข้อกำหนดของ executor เดิม
  ซึ่งเพิ่มแบบหยุดไว้ สั่ง recheck และไม่ resume อัตโนมัติ
- ปัจจุบัน executor สำหรับเพิ่ม hash ใหม่จะระงับการเพิ่มเมื่อมี rTorrent ที่เปิดอยู่
  เพราะยังตรวจ duplicate ข้าม client ทั้งหมดผ่านเส้นทางนี้ไม่ได้
  การค้นหาและ preflight ไม่จำเป็นต้องปิด rTorrent

การเพิ่ม adapter นี้ไม่เปลี่ยนสวิตช์ execution, ไม่ยกเลิก H&R/Quest และไม่ให้สิทธิ์
Cleaner ลบไฟล์ การบันทึกผลที่ยืนยันแล้วใช้ mapping กลางและ schema เดิม
ตาม [TORRENT_MAPPING.md](TORRENT_MAPPING.md)
