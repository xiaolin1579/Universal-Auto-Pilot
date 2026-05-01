
# 🚀 Universal Auto-Pilot

**Universal Auto-Pilot** คือระบบ Automation อัจฉริยะที่ช่วยบริหารจัดการการดาวน์โหลด Torrent จากเว็บไซต์ BearBit และ TorrentDD โดยเน้นความสะดวกสบาย ความรวดเร็ว และการรักษาค่า Ratio อย่างมีประสิทธิภาพ พัฒนาด้วย Python และ Playwright พร้อมระบบบริหารจัดการพื้นที่จัดเก็บข้อมูลอัตโนมัติ

---

## ✨ คุณสมบัติเด่น (Key Features)

*   **🔍 Smart Auto-Downloader:** ค้นหาและคัดเลือก Torrent ตามหมวดหมู่ (Target URLs) ที่คุณต้องการอัตโนมัติ
*   **⚙️ Advanced Filtering:** กรองไฟล์ตามขนาด (Min/Max Size) และสถานะ **Freeload** พร้อมระบบเช็คสถานะ **Pending (รออนุมัติฟรี)** ในหน้ารายละเอียดเพื่อความแม่นยำ
*   **🤖 Multi-Node Distribution:** รองรับการเชื่อมต่อทั้ง **qBittorrent** และ **rTorrent** (XML-RPC) พร้อมอัลกอริทึมเลือก Node ตามน้ำหนักงาน (Weight Cap) และความเร็วของ Disk (NVMe/SSD/HDD)
*   **🧹 Intelligent Auto-Clean & Reclaim:**
    *   **Auto-Clean:** ลบไฟล์อัตโนมัติเมื่อถึงเป้าหมาย Ratio หรือระยะเวลาที่กำหนด
    *   **Smart Reclaim:** ระบบกู้คืนพื้นที่ดิสก์ด่วนเมื่อพื้นที่เหลือน้อยกว่า 15GB เพื่อให้ดาวน์โหลดงานใหม่ได้ต่อเนื่อง
*   **🗳️ Auto-Gratitude & Vote:** ระบบกด **"ขอบคุณ" (Thanks)** และ **"โหวตคะแนนยอดเยี่ยม"** อัตโนมัติเพื่อสนับสนุนผู้อัปโหลด
*   **📊 Advanced Statistics & Reports:**
    *   รายงานสถิติการอัปโหลด/ดาวน์โหลดรายชั่วโมง และส่วนต่าง (Changes) ที่เกิดขึ้น
    *   ระบบสรุปยอดงานประจำวันและประจำเดือนผ่าน Telegram
*   **🔔 Universal Notifications:** แจ้งเตือนผ่าน **Telegram, LINE Notify, และ Discord DM**
*   **🔄 Remote Management:** ควบคุมบอทผ่าน Telegram (Start/Stop/Status/Config) ได้ทันที

---

## 🛠 การติดตั้งและเริ่มต้นใช้งาน (Getting Started)

### 1. เตรียมความพร้อม (Prerequisites)
เครื่องของคุณต้องติดตั้ง **Python 3.8+** และเบราว์เซอร์สำหรับ Playwright:

```bash
# ติดตั้ง Library ที่จำเป็น
pip install -r requirements.txt

# ติดตั้ง Browser Engine สำหรับ Playwright
playwright install chromium
```

### 2. การตั้งค่า (Configuration)
คัดลอกไฟล์ตัวอย่างและแก้ไขข้อมูลใน `config.json` (ห้ามอัปโหลดไฟล์นี้ขึ้นที่สาธารณะเด็ดขาด):

```bash
cp config.json.example config.json
```

### 3. เริ่มรันโปรแกรม (Execution)
```bash
# เริ่มระบบสแกนและจัดการไฟล์อัตโนมัติ
python main.py

# เริ่มระบบควบคุมระยะไกลผ่าน Telegram/Discord (ถ้าต้องการ)
python remote_control.py
```

---

## 📂 โครงสร้างไฟล์ที่สำคัญ

*   `main.py`: สคริปต์หลัก จัดการสแกน, ดาวน์โหลด, โหวต และลบไฟล์
*   `remote_control.py`: ระบบควบคุมระยะไกลและรายงานสถิติ
*   `config.json`: ไฟล์ตั้งค่าหลัก (รหัสผ่าน, Tokens, เงื่อนไขการกรอง)
*   `stats_history.json`: ฐานข้อมูลสถิติย้อนหลัง 31 วัน (744 จุดข้อมูล)
*   `seen.txt` / `hash_seen.txt`: ป้องกันการโหลดไฟล์ซ้ำ

---

## ⚠️ ข้อควรระวัง (Disclaimer)
โปรแกรมนี้สร้างขึ้นเพื่ออำนวยความสะดวกในการบริหารจัดการข้อมูล ผู้ใช้งานควรตั้งค่า `MIN_WAIT_MINUTES` ให้เหมาะสม (แนะนำ 5-10 นาทีขึ้นไป) เพื่อไม่ให้เป็นการรบกวนการทำงานของเซิร์ฟเวอร์เว็บไซต์ต้นทาง

---

**Developed with ❤️ for the ฺBearBit and TorrentDD Community.**
