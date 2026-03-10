# src/ui/schedule_view.py
# Sınav Programı: listeleme + otomatik plan + çakışma kontrolü + elle düzenleme + otomatik oda atama

import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm

from core.db import get_conn


class ScheduleView(ttk.Frame):
    def __init__(self, master, user, **kwargs):
        super().__init__(master, **kwargs)
        self.user = user

        # ÜST BAR
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=8)
        ttk.Button(bar, text="Dışa Aktar (Excel)", command=self.export_excel).pack(side="left", padx=8)
        ttk.Button(bar, text="Otomatik Planla (taslak)", command=self.auto_plan).pack(side="left")
        ttk.Button(bar, text="Çakışma Hesapla", command=self.check_conflicts).pack(side="left", padx=8)
        ttk.Button(bar, text="Temizle (sınavları sil)", command=self.clear_plan).pack(side="left", padx=8)
        ttk.Button(bar, text="Otomatik Oda Ata", command=self.auto_assign_rooms).pack(side="left", padx=8)
        ttk.Button(bar, text="Saat/Derslik Düzenle", command=self.edit_selected_exam).pack(side="left", padx=8)
        ttk.Button(bar, text="PDF olarak Kaydet", command=self.export_pdf).pack(side="left", padx=8)
        ttk.Button(bar, text="Programı PDF", command=self.export_program_pdf).pack(side="left", padx=8)
        ttk.Button(bar, text="Kısıtlar", command=self.open_constraints).pack(side="left", padx=8)
        ttk.Button(bar, text="Oturma Planı", command=self.open_seating).pack(side="left", padx=8)

        # Kısıtlar için varsayılanlar
        self.constraints = {
            "date_start": None,             # datetime.date
            "date_end": None,               # datetime.date
            "exclude_days": set(),          # {5,6} -> Cts/Paz
            "default_duration": 75,         # dk
            "cooldown_min": 15,             # öğrenci başına min bekleme (dk)
            "single_exam_at_a_time": False, # aynı anda yalnızca tek sınav
            "exam_type": "Vize",            # not: şimdilik kayıt amaçlı
            "excluded_courses": set(),
        }

        # BİLGİ ETİKETİ
        self.info = ttk.Label(self, text="")
        self.info.pack(anchor="w", padx=10, pady=(0, 4))

        # TreeView: gizli exam_id ilk sütun (PDF gereği satırdan tekil kimlik ile çalışacağız)
        cols = ("exam_id", "course", "name", "year", "start", "room")
        headers = ("ID", "Kod", "Ad", "Sınıf", "Başlangıç", "DerslikID")

        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=18)
        for c, h in zip(cols, headers):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=100)
        # ID’yi GİZLE
        self.tree.column("exam_id", width=0, minwidth=0, stretch=False)

        self.tree.pack(fill="both", expand=True, padx=10, pady=8)
        # Çift tıkla düzenleme (detay penceresi)
        self.tree.bind("<Double-1>", lambda e: self.edit_selected_exam())

        self.refresh()

    # ----------------- YARDIMCI -----------------

    def _dept_clause(self, table_alias: str):
        role = (self.user or {}).get("role")
        dept_id = (self.user or {}).get("department_id")
        if role == "admin" or dept_id is None:
            return "", ()
        return f" AND {table_alias}.dept_id=?", (dept_id,)

    # ----------------- TEMEL İŞLEMLER -----------------

    def refresh(self):
        # tabloyu temizle
        for i in self.tree.get_children():
            self.tree.delete(i)

        where_dept, dept_params = self._dept_clause("c")
        # 👇 exam_id mutlaka ilk sütunda dönüyor
        sql = f"""
            SELECT
                e.id           AS exam_id,
                c.code         AS course,
                c.name         AS name,
                c.class_year   AS class_year,
                e.exam_start   AS exam_start,
                e.room_id      AS room_id
            FROM courses c
            LEFT JOIN exams e ON e.course_id = c.id
            WHERE 1=1
            {where_dept}
            ORDER BY c.class_year, c.code
        """
        with get_conn() as con:
            cur = con.cursor()
            cur.execute(sql, dept_params)
            rows = cur.fetchall()

        # rows: (exam_id, course, name, class_year, exam_start, room_id)
        for r in rows:
            exam_id, course, name, class_year, exam_start, room_id = r
            self.tree.insert("", "end", values=(exam_id, course, name, class_year, exam_start, room_id))

        planned = sum(1 for r in rows if r[4])  # r[4] = exam_start
        self.info.config(text=f"Toplam ders: {len(rows)} | Planlanan sınav: {planned}")

    def export_program_pdf(self):
        """
        PDF’te bu şekilde isteniyor:
          - Bölüm bazlı sınav programı
          - Gün başlıklarına göre gruplama (YYYY-MM-DD + hafta günü)
          - Sütunlar: Saat, Kod, Ad, Sınıf, Derslik
          - Sayfa taşarsa başlıkları tekrar çiz
        """
        try:
            from reportlab.lib.pagesizes import A4, landscape
            from reportlab.pdfgen import canvas
            from reportlab.lib.units import cm
        except Exception:
            messagebox.showerror("PDF", "reportlab kurulu değil. Kur: pip install reportlab")
            return

        dept_id = self.user.get("department_id") or 1

        # Bölüm adı (varsa) — yoksa "Bölüm <id>"
        with get_conn() as con:
            cur = con.cursor()
            dept_name = None
            try:
                cur.execute("SELECT name FROM departments WHERE id=?", (dept_id,))
                r = cur.fetchone()
                if r and r[0]:
                    dept_name = r[0]
            except Exception:
                pass
            if not dept_name:
                dept_name = f"Bölüm {dept_id}"

            # Program verisi
            cur.execute("""
                SELECT
                    DATE(e.exam_start) AS d,
                    TIME(e.exam_start) AS t,
                    c.code,
                    c.name,
                    c.class_year,
                    COALESCE(cl.code,'') AS room
                FROM exams e
                JOIN courses c ON c.id = e.course_id
                LEFT JOIN classrooms cl ON cl.id = e.room_id
                WHERE c.dept_id=?
                ORDER BY d, t, c.class_year, c.code
            """, (dept_id,))
            rows = cur.fetchall()

        if not rows:
            messagebox.showinfo("Programı PDF", "Kaydedilecek sınav bulunamadı.")
            return

        # Tarih aralığı (başlık altı için)
        dates = [r[0] for r in rows if r[0]]
        date_span = ""
        if dates:
            dmin = min(dates)
            dmax = max(dates)
            date_span = f"{dmin} — {dmax}"

        # PDF kurulumu
        out_dir = "data"
        import os
        os.makedirs(out_dir, exist_ok=True)
        fname = f"sinav_programi_{dept_id}_{datetime.now():%Y%m%d_%H%M}.pdf"
        out_path = os.path.join(out_dir, fname)

        c = canvas.Canvas(out_path, pagesize=landscape(A4))
        page_w, page_h = landscape(A4)

        left = 1.6 * cm
        right = 1.6 * cm
        top = 1.6 * cm
        bottom = 1.2 * cm

        def weekday_tr(dstr: str) -> str:
            # YYYY-MM-DD → haftanın günü (Tr kısa)
            try:
                d = datetime.strptime(dstr, "%Y-%m-%d").date()
                names = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cts", "Paz"]
                return names[d.weekday()]
            except Exception:
                return ""

        title = "Sınav Programı"
        subtitle = f"{dept_name}"
        if date_span:
            subtitle += f"  •  {date_span}"
        # constraints.exam_type bilgisi varsa (ör. “Vize/Final”), başlığa ekleyelim
        etype = (getattr(self, "constraints", {}) or {}).get("exam_type")
        if etype:
            subtitle += f"  •  {etype}"

        # Sütun düzeni
        headers = ["Saat", "Kod", "Ad", "Sınıf", "Derslik"]
        widths = [3.0 * cm, 3.5 * cm, 13.0 * cm, 2.5 * cm, 3.5 * cm]
        x0 = left
        x_positions = [x0]
        for w in widths[:-1]:
            x_positions.append(x_positions[-1] + w)

        line_h = 0.6 * cm

        def draw_page_header():
            y = page_h - top
            c.setFont("Helvetica-Bold", 16)
            c.drawString(left, y, title)
            c.setFont("Helvetica", 10)
            c.drawRightString(page_w - right, y, f"Oluşturma: {datetime.now():%Y-%m-%d %H:%M}")
            y -= 0.7 * cm
            c.setFont("Helvetica", 11)
            c.drawString(left, y, subtitle)
            return y - 0.5 * cm

        def draw_day_header(day_str, y):
            # Gün başlığı
            c.setFont("Helvetica-Bold", 11)
            wd = weekday_tr(day_str)
            c.drawString(left, y, f"{day_str}  ({wd})")
            y -= 0.35 * cm
            # Tablo başlıkları
            c.setFont("Helvetica-Bold", 9)
            for i, h in enumerate(headers):
                c.drawString(x_positions[i], y, h)
            y -= 0.2 * cm
            c.line(left, y, page_w - right, y)
            return y - 0.2 * cm

        def ensure_space(y, need_lines=1, with_header_for_day=None):
            # need_lines satır için yer var mı? yoksa sayfa kır.
            nonlocal c
            needed = need_lines * line_h + 1.2 * cm  # alt boşluk tamponu
            if y - needed < bottom:
                c.showPage()
                return draw_page_header(), True  # new page, y
            return y, False

        y = draw_page_header()

        # Gün bazında gruplama
        from itertools import groupby
        def key_day(r):
            return r[0]  # d

        for day, group in groupby(rows, key=key_day):
            # Sayfa/başlık kontrol
            y, _ = ensure_space(y, need_lines=3)
            y = draw_day_header(day, y)

            c.setFont("Helvetica", 9)
            for rec in list(group):
                # satır: [d, t, code, name, class_year, room]
                _d, t, code, name, cy, room = rec
                # yeni sayfa gerekiyorsa + gün başlığını tekrar çiz
                y, newp = ensure_space(y, need_lines=1)
                if newp:
                    y = draw_day_header(day, y)

                vals = [
                    (t or "")[:5],  # Saat HH:MM
                    str(code or ""),
                    str(name or "")[:90],
                    str(cy or ""),
                    str(room or ""),
                ]
                # yaz
                for i, val in enumerate(vals):
                    c.drawString(x_positions[i], y, val)
                y -= line_h

        c.showPage()
        c.save()

        messagebox.showinfo("Programı PDF", f"PDF başarıyla kaydedildi:\n{out_path}")

    def export_seating_pdf(self):
        messagebox.showinfo("Oturma Planı (PDF)", "Oturma planı PDF çıktısı bu sürümde devre dışı.")

    def clear_plan(self):
        """Sınav kayıtlarını siler (admin: tüm bölümler, koordinator: kendi bölümü)."""
        role = (self.user or {}).get("role")
        dept_id = (self.user or {}).get("department_id")

        with get_conn() as con:
            cur = con.cursor()
            if role == "admin" or dept_id is None:
                cur.execute("DELETE FROM exams")
            else:
                cur.execute("""
                    DELETE FROM exams
                    WHERE course_id IN (SELECT id FROM courses WHERE dept_id = ?)
                """, (dept_id,))
        self.refresh()
        messagebox.showinfo("Bilgi", "Sınav kayıtları silindi.")

    # ----------------- ÇAKIŞMA KONTROL -----------------

    def check_conflicts(self):
        """
        Aynı anda (aynı exam_start) sınavı olan farklı dersler arasında,
        ortak öğrencileri bul ve raporla.
        - enrollments üzerindeki olası mükerrerleri DISTINCT ile kırpıyoruz
        - bölüm filtresi uyguluyoruz
        """
        dept_id = self.user.get("department_id") or 1

        with get_conn() as con:
            cur = con.cursor()

            # Örnek liste için detaylı satırlar
            cur.execute("""
                WITH enroll AS (
                    SELECT DISTINCT student_id, course_id
                    FROM enrollments
                )
                SELECT
                    s.number,
                    s.full_name,
                    c1.code AS course1,
                    c2.code AS course2,
                    ex1.exam_start
                FROM enroll e1
                JOIN enroll e2
                     ON e1.student_id = e2.student_id
                    AND e1.course_id  < e2.course_id
                JOIN exams  ex1 ON ex1.course_id = e1.course_id
                JOIN exams  ex2 ON ex2.course_id = e2.course_id
                               AND ex1.exam_start = ex2.exam_start
                JOIN courses c1 ON c1.id = e1.course_id AND c1.dept_id = ?
                JOIN courses c2 ON c2.id = e2.course_id AND c2.dept_id = ?
                JOIN students s ON s.id = e1.student_id
                ORDER BY ex1.exam_start, s.number
            """, (dept_id, dept_id))
            rows = cur.fetchall()

            # Zaman dilimlerine göre özet
            cur.execute("""
                WITH enroll AS (
                    SELECT DISTINCT student_id, course_id
                    FROM enrollments
                ),
                base AS (
                    SELECT ex1.exam_start AS ts
                    FROM enroll e1
                    JOIN enroll e2
                         ON e1.student_id = e2.student_id
                        AND e1.course_id  < e2.course_id
                    JOIN exams  ex1 ON ex1.course_id = e1.course_id
                    JOIN exams  ex2 ON ex2.course_id = e2.course_id
                                   AND ex1.exam_start = ex2.exam_start
                    JOIN courses c1 ON c1.id = e1.course_id AND c1.dept_id = ?
                    JOIN courses c2 ON c2.id = e2.course_id AND c2.dept_id = ?
                )
                SELECT ts, COUNT(*) AS cnt
                FROM base
                GROUP BY ts
                ORDER BY ts
            """, (dept_id, dept_id))
            per_slot = cur.fetchall()

        if not rows:
            messagebox.showinfo("Çakışma Kontrolü", "✅ Hiç çakışma bulunamadı.")
            return

        total = len(rows)
        parts = [f"⚠️ {total} çakışma bulundu.\n"]
        if per_slot:
            parts.append("— Zamanlara göre sayım —")
            for ts, cnt in per_slot:
                parts.append(f"  {ts}: {cnt}")
            parts.append("")

        parts.append("Örnekler (ilk 5):")
        for num, ad, c1, c2, ts in rows[:5]:
            parts.append(f"  {num} - {ad}: {c1} ve {c2} ({ts})")

        messagebox.showwarning("Çakışma Detayı", "\n".join(parts))



    # ----------------- OTOMATİK PLAN -----------------

    def auto_plan(self):
        """
        Çakışma-farkında basit yerleştirici:
        - Slotlar: 10 gün * [09:00, 11:00, 13:30, 15:30, 17:00, 19:00]
        - Dersler, öğrencisi ortak olduğu derslerle aynı anda olmadan yerleştirilir.
        """
        from datetime import datetime, timedelta

        dept_id = self.user.get("department_id") or 1

        # --- SLOT HAVUZU (Kısıtlar varsa onlara göre üret)
        daily_times = [(9, 0), (11, 0), (13, 30), (15, 30), (17, 0), (19, 0)]
        slots = []

        c = getattr(self, "constraints", None)
        if c and c.get("date_start") and c.get("date_end"):
            cur_day = c["date_start"]
            while cur_day <= c["date_end"]:
                if cur_day.weekday() not in c.get("exclude_days", set()):
                    for h, m in daily_times:
                        slots.append(datetime(cur_day.year, cur_day.month, cur_day.day, h, m))
                cur_day += timedelta(days=1)
        else:
            # Varsayılan: bugün + 10 gün
            start_day = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
            days = [start_day + timedelta(days=d) for d in range(10)]
            for d in days:
                for h, m in daily_times:
                    slots.append(d.replace(hour=h, minute=m))

        with get_conn() as con:
            cur = con.cursor()

            # 2) Bu bölümün dersleri (id, kod)
            cur.execute("""
                SELECT id, code
                FROM courses
                WHERE dept_id=?
                ORDER BY code
            """, (dept_id,))
            courses = cur.fetchall()  # [(cid, code), ...]

            # 3) Temizle (sadece bu bölüm derslerinin examlarını)
            role = (self.user or {}).get("role")
            if role == "admin":
                cur.execute("DELETE FROM exams")
            else:
                cur.execute("""
                    DELETE FROM exams
                    WHERE course_id IN (SELECT id FROM courses WHERE dept_id=?)
                """, (dept_id,))

            # 4) Her dersin öğrenci kümesi ve ders büyüklüğü
            course_students = {}  # cid -> set(student_id)
            course_sizes = {}     # cid -> int
            for cid, _ in courses:
                cur.execute("SELECT student_id FROM enrollments WHERE course_id=?", (cid,))
                sids = {r[0] for r in cur.fetchall()}
                course_students[cid] = sids
                course_sizes[cid] = len(sids)

            # 5) Çakışma grafı
            neighbors = {cid: set() for cid, _ in courses}
            cids = [cid for cid, _ in courses]
            for i in range(len(cids)):
                a = cids[i]
                Sa = course_students[a]
                for j in range(i + 1, len(cids)):
                    b = cids[j]
                    if not Sa or not course_students[b]:
                        continue
                    if Sa.intersection(course_students[b]):
                        neighbors[a].add(b)
                        neighbors[b].add(a)

            # 6) Yerleştirme sırası
            order = sorted(
                cids,
                key=lambda x: (course_sizes[x], len(neighbors[x])),
                reverse=True
            )

            # 7) Greedy yerleştirme
            placed_time = {}          # cid -> slot(datetime)
            used_by_slot = {}         # slot -> set(cid)
            last_exam = {}            # student_id -> datetime (cooldown için)

            for cid in order:
                forbiddens = set()
                for nb in neighbors[cid]:
                    if nb in placed_time:
                        forbiddens.add(placed_time[nb])

                chosen = None
                for ts in slots:
                    if ts in forbiddens:
                        continue

                    # Tek sınav modu
                    if self.constraints.get("single_exam_at_a_time", False) and used_by_slot.get(ts):
                        continue

                    # aynı anda aynı öğrenciyi engelle
                    ok = True
                    for other in used_by_slot.get(ts, set()):
                        if course_students[cid] & course_students[other]:
                            ok = False
                            break
                    if not ok:
                        continue

                    # Bekleme süresi (dk)
                    cooldown = int(self.constraints.get("cooldown_min", 0) or 0)
                    if cooldown > 0:
                        for sid in course_students[cid]:
                            last = last_exam.get(sid)
                            if last is not None:
                                delta_min = abs((ts - last).total_seconds()) / 60.0
                                if delta_min < cooldown:
                                    ok = False
                                    break
                        if not ok:
                            continue

                    chosen = ts
                    break

                if chosen is None:
                    # slot havuzu yetmedi → son slot
                    chosen = slots[-1]

                placed_time[cid] = chosen
                used_by_slot.setdefault(chosen, set()).add(cid)
                for sid in course_students[cid]:
                    last_exam[sid] = chosen

            # 8) Veritabanına yaz
            for cid, ts in placed_time.items():
                cur.execute("INSERT INTO exams(course_id, exam_start) VALUES (?, ?)", (cid, ts))

        self.refresh()
        messagebox.showinfo("Tamam", "Çakışma-farkında taslak sınav planı oluşturuldu.")

    # ----------------- ELLE DÜZENLEME (Çift tık) -----------------

    def edit_selected_exam(self):
        """Seçili satır için sınav başlangıç/oda düzenleme penceresi."""
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Uyarı", "Önce bir sınav satırı seçin.")
            return

        values = self.tree.item(sel[0], "values")
        if not values:
            messagebox.showerror("Hata", "Satır okunamadı.")
            return

        # Yeni sütun sırası: (exam_id, course(code), name, class_year, exam_start, room_id)
        exam_id = values[0]
        code    = values[1]
        name    = values[2]
        year    = values[3]
        start   = values[4]
        room_id = values[5]

        dept_id = self.user.get("department_id") or 1

        with get_conn() as con:
            cur = con.cursor()
            # course_id
            cur.execute("SELECT id FROM courses WHERE code=? AND dept_id=?", (code, dept_id))
            row = cur.fetchone()
            if not row:
                messagebox.showerror("Hata", "Ders bulunamadı.")
                return
            course_id = row[0]

            # mevcut exam (varsa id üzerinden, yoksa course_id ile)
            if exam_id:
                try:
                    cur.execute("SELECT id, exam_start, room_id FROM exams WHERE id=?", (int(exam_id),))
                except Exception:
                    cur.execute("SELECT id, exam_start, room_id FROM exams WHERE id=?", (exam_id,))
            else:
                cur.execute("SELECT id, exam_start, room_id FROM exams WHERE course_id=?", (course_id,))
            ex = cur.fetchone()

            exam_id_val = ex[0] if ex else None
            exam_start = ex[1] if ex else (datetime.now().replace(microsecond=0).strftime("%Y-%m-%d %H:%M"))
            exam_room = ex[2] if ex else None

            # derslikler
            cur.execute("""
                SELECT id, code, name, capacity
                FROM classrooms
                WHERE dept_id=?
                ORDER BY code
            """, (dept_id,))
            rooms = cur.fetchall()

        # Pencere
        win = tk.Toplevel(self)
        win.title(f"Sınav Düzenle — {code}")
        win.geometry("420x200")
        win.transient(self.winfo_toplevel())
        win.grab_set()

        frm = ttk.Frame(win); frm.pack(fill="both", expand=True, padx=12, pady=12)

        ttk.Label(frm, text="Tarih-Saat (YYYY-MM-DD HH:MM):").grid(row=0, column=0, sticky="e", padx=6, pady=6)
        v_start = tk.StringVar(value=(exam_start or ""))
        ttk.Entry(frm, textvariable=v_start, width=24).grid(row=0, column=1, sticky="w", padx=6, pady=6)

        ttk.Label(frm, text="Derslik:").grid(row=1, column=0, sticky="e", padx=6, pady=6)
        room_disp_list = ["(boş bırak)"] + [f"{r[0]} — {r[1]} ({r[2]}) cap:{r[3]}" for r in rooms]
        pre = "(boş bırak)"
        if exam_room:
            for s in room_disp_list:
                if s.startswith(str(exam_room) + " —"):
                    pre = s; break
        v_room = tk.StringVar(value=pre)
        ttk.Combobox(frm, textvariable=v_room, values=room_disp_list, state="readonly", width=36) \
            .grid(row=1, column=1, sticky="w", padx=6, pady=6)

        btnf = ttk.Frame(win); btnf.pack(fill="x", padx=12, pady=(0, 12))

        def _save():
            val_start = v_start.get().strip()
            try:
                _ = datetime.strptime(val_start, "%Y-%m-%d %H:%M")
            except Exception:
                messagebox.showerror("Hata", "Tarih formatı hatalı. Örn: 2025-01-15 13:30")
                return

            sel_text = v_room.get()
            new_room_id = None
            if sel_text != "(boş bırak)":
                try:
                    new_room_id = int(sel_text.split(" — ")[0])
                except Exception:
                    new_room_id = None

            with get_conn() as con2:
                cur2 = con2.cursor()
                if exam_id_val:
                    # Güncelle (id ile)
                    cur2.execute("UPDATE exams SET exam_start=?, room_id=? WHERE id=?",
                                 (val_start, new_room_id, exam_id_val))
                else:
                    # Yoksa course_id ile yeni kayıt
                    cur2.execute("INSERT INTO exams(course_id, exam_start, room_id) VALUES (?,?,?)",
                                 (course_id, val_start, new_room_id))

            self.refresh()
            win.destroy()

        ttk.Button(btnf, text="Kaydet", command=_save).pack(side="right")
        ttk.Button(btnf, text="İptal", command=win.destroy).pack(side="right", padx=8)

    # ----------------- OTOMATİK ODA ATAMA -----------------

    def auto_assign_rooms(self):
        """
        Her sınav için, aynı anda boş olan ve kapasitesi yeten bir derslik ata.
        Kapasite yetmiyorsa atlama ve raporla.
        """
        dept_id = self.user.get("department_id") or 1
        assigned = 0
        skipped_no_room = 0
        skipped_capacity = 0
        examples_capacity = []  # (code, need, maxcap, ts)
        examples_noroom = []    # (code, need, ts)

        with get_conn() as con:
            cur = con.cursor()

            # Odalar: kapasite DESC (büyükten küçüğe)
            cur.execute("""
                SELECT id, code, capacity
                FROM classrooms
                WHERE dept_id=?
                ORDER BY capacity DESC, code ASC
            """, (dept_id,))
            rooms = cur.fetchall()
            if not rooms:
                messagebox.showwarning("Oda Atama", "Bu bölüm için kayıtlı derslik yok.")
                return

            # Oda atanmamış sınavlar + öğrenci sayısı + ders kodu
            cur.execute("""
                SELECT e.id, e.course_id, e.exam_start, c.code,
                       (SELECT COUNT(*) FROM enrollments en WHERE en.course_id=e.course_id) AS need
                FROM exams e
                JOIN courses c ON c.id = e.course_id
                WHERE c.dept_id=? AND e.room_id IS NULL
                ORDER BY e.exam_start, c.class_year, c.code
            """, (dept_id,))
            exams = cur.fetchall()

            # aynı anda kullanılan odalar
            cur.execute("SELECT exam_start, room_id FROM exams WHERE room_id IS NOT NULL")
            used_by_ts = {}
            for ts, rid in cur.fetchall():
                used_by_ts.setdefault(ts, set()).add(rid)

            for ex_id, course_id, ts, code, need in exams:
                used = used_by_ts.get(ts, set())

                # bu ts'te boş olan odalar
                candidates = [(rid, rcode, cap) for (rid, rcode, cap) in rooms if rid not in used]
                if not candidates:
                    skipped_no_room += 1
                    examples_noroom.append((code, need, ts))
                    continue

                # kapasitesi yetenler
                fits = [(rid, rcode, cap) for (rid, rcode, cap) in candidates if cap >= need]
                if not fits:
                    # hiçbir boş odanın kapasitesi yetmiyor → kapasite raporla
                    maxcap = max(c[2] for c in candidates) if candidates else 0
                    skipped_capacity += 1
                    examples_capacity.append((code, need, maxcap, ts))
                    continue

                # en az kapasiteli uygun odayı tercih et (daha verimli)
                fits.sort(key=lambda x: x[2])
                rid, _, _ = fits[0]
                cur.execute("UPDATE exams SET room_id=? WHERE id=?", (rid, ex_id))
                used_by_ts.setdefault(ts, set()).add(rid)
                assigned += 1

            con.commit()

        # Rapor
        lines = [f"Atanan: {assigned}"]
        if skipped_no_room or skipped_capacity:
            lines.append(f"Atlananan (boş oda yok): {skipped_no_room}")
            lines.append(f"Atlananan (kapasite yetersiz): {skipped_capacity}")
        if examples_capacity:
            ex = "\n".join([f"- {c} iht: {n}, max boş kapasite: {mx} @ {ts}"
                            for c, n, mx, ts in examples_capacity[:5]])
            lines.append("\nKapasite yetersiz örnekler (ilk 5):\n" + ex)
        if examples_noroom:
            ex = "\n".join([f"- {c} iht: {n} @ {ts}" for c, n, ts in examples_noroom[:5]])
            lines.append("\nBoş oda yok örnekler (ilk 5):\n" + ex)

        messagebox.showinfo("Oda Atama", "\n".join(lines))
        self.refresh()

    # ----------------- DIŞA AKTAR -----------------

    def export_excel(self):
        """Ekrandaki planı Excel'e kaydet."""
        dept_id = self.user.get("department_id") or 1
        with get_conn() as con:
            cur = con.cursor()
            cur.execute("""
                SELECT c.code AS Kod,
                       c.name AS Ad,
                       c.class_year AS Sınıf,
                       e.exam_start AS Başlangıç,
                       COALESCE(cl.code, '') AS Derslik
                FROM courses c
                LEFT JOIN exams e ON e.course_id=c.id
                LEFT JOIN classrooms cl ON cl.id = e.room_id
                WHERE c.dept_id=?
                ORDER BY c.class_year, c.code
            """, (dept_id,))
            rows = cur.fetchall()

        if not rows:
            messagebox.showinfo("Dışa Aktar", "Aktarılacak kayıt bulunamadı.")
            return

        df = pd.DataFrame(rows, columns=["Kod", "Ad", "Sınıf", "Başlangıç", "Derslik"])
        out_dir = Path("data"); out_dir.mkdir(exist_ok=True)
        out_path = out_dir / f"sinav_plani_{datetime.now():%Y%m%d_%H%M}.xlsx"
        df.to_excel(out_path.as_posix(), index=False)
        messagebox.showinfo("Dışa Aktar", f"Excel dosyası kaydedildi:\n{out_path}")

    def export_pdf(self):
        """Sınav planını PDF olarak dışa aktar."""
        dept_id = self.user.get("department_id") or 1
        with get_conn() as con:
            cur = con.cursor()
            cur.execute("""
                SELECT c.code, c.name, c.class_year, e.exam_start, COALESCE(cl.code, '')
                FROM courses c
                LEFT JOIN exams e ON e.course_id=c.id
                LEFT JOIN classrooms cl ON cl.id = e.room_id
                WHERE c.dept_id=?
                ORDER BY c.class_year, e.exam_start
            """, (dept_id,))
            rows = cur.fetchall()

        if not rows:
            messagebox.showinfo("PDF", "Kaydedilecek sınav bulunamadı.")
            return

        pdf_path = f"data/sinav_programi_{datetime.now():%Y%m%d_%H%M}.pdf"
        c = canvas.Canvas(pdf_path, pagesize=landscape(A4))
        c.setFont("Helvetica-Bold", 16)
        c.drawString(2 * cm, 19 * cm, "Sınav Programı")

        headers = ["Kod", "Ad", "Sınıf", "Başlangıç", "Derslik"]
        c.setFont("Helvetica-Bold", 10)
        y = 18 * cm
        for i, h in enumerate(headers):
            c.drawString((2 + i * 6) * cm, y, h)

        c.setFont("Helvetica", 9)
        y -= 0.8 * cm
        for code, name, year, start, room in rows:
            c.drawString(2 * cm, y, str(code))
            c.drawString(8 * cm, y, str(name)[:40])
            c.drawString(17 * cm, y, str(year))
            c.drawString(20 * cm, y, str(start))
            c.drawString(27 * cm, y, str(room))
            y -= 0.6 * cm
            if y < 2 * cm:
                c.showPage()
                c.setFont("Helvetica", 9)
                y = 18 * cm

        c.save()
        messagebox.showinfo("PDF", f"PDF başarıyla kaydedildi:\n{pdf_path}")

    # ----------------- DİĞER PDF’LER / YARDIMCILAR -----------------

    def _get_selected_course_id(self):
        sel = self.tree.selection()
        if not sel:
            return None
        values = self.tree.item(sel[0], "values")
        # Yeni düzende code = values[1]
        code = values[1]
        with get_conn() as con:
            cur = con.cursor()
            cur.execute("SELECT id FROM courses WHERE dept_id=? AND code=?",
                        (self.user.get("department_id") or 1, code))
            row = cur.fetchone()
            return row[0] if row else None

    def edit_selected(self):
        # Eski ismi çağıran yerler için yönlendirme
        return self.edit_selected_exam()

    # ----------------- KISITLAR -----------------

    def open_constraints(self):
        top = tk.Toplevel(self)
        top.title("Kısıtlar")
        top.geometry("520x420")

        # --- Girdi değişkenleri
        v_start = tk.StringVar(value="")
        v_end = tk.StringVar(value="")
        v_cool = tk.StringVar(value=str(self.constraints.get("cooldown_min", 15)))
        v_defdur = tk.StringVar(value=str(self.constraints.get("default_duration", 75)))
        v_single = tk.BooleanVar(value=self.constraints.get("single_exam_at_a_time", False))

        # Tarih alanları
        frm_dates = ttk.LabelFrame(top, text="Tarih Aralığı")
        frm_dates.pack(fill="x", padx=10, pady=8)
        ttk.Label(frm_dates, text="Başlangıç (YYYY-MM-DD):").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        ttk.Entry(frm_dates, textvariable=v_start, width=16).grid(row=0, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(frm_dates, text="Bitiş (YYYY-MM-DD):").grid(row=1, column=0, sticky="e", padx=6, pady=4)
        ttk.Entry(frm_dates, textvariable=v_end, width=16).grid(row=1, column=1, sticky="w", padx=6, pady=4)

        # Hariç günler (hafta içi: 0-4, Cmt:5, Paz:6)
        frm_days = ttk.LabelFrame(top, text="Hariç Günler")
        frm_days.pack(fill="x", padx=10, pady=8)
        v_excl = {d: tk.BooleanVar(value=(d in self.constraints.get("exclude_days", set()))) for d in range(7)}
        text_map = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cts", "Paz"]
        for i in range(7):
            ttk.Checkbutton(frm_days, text=text_map[i], variable=v_excl[i]).grid(row=0, column=i, padx=4, pady=4)

        # Süre & bekleme
        frm_dur = ttk.LabelFrame(top, text="Süre ve Bekleme")
        frm_dur.pack(fill="x", padx=10, pady=8)
        ttk.Label(frm_dur, text="Varsayılan süre (dk):").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        ttk.Entry(frm_dur, textvariable=v_defdur, width=8).grid(row=0, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(frm_dur, text="Bekleme süresi (dk):").grid(row=1, column=0, sticky="e", padx=6, pady=4)
        ttk.Entry(frm_dur, textvariable=v_cool, width=8).grid(row=1, column=1, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(frm_dur, text="Aynı anda yalnızca tek sınav", variable=v_single).grid(row=2, column=0,
                                                                                              columnspan=2, sticky="w",
                                                                                              padx=6, pady=6)

        # Kaydet
        btns = ttk.Frame(top); btns.pack(fill="x", padx=10, pady=8)

        def _save():
            ds = v_start.get().strip()
            de = v_end.get().strip()
            try:
                d_start = datetime.strptime(ds, "%Y-%m-%d").date() if ds else None
                d_end   = datetime.strptime(de, "%Y-%m-%d").date() if de else None
            except ValueError:
                messagebox.showerror("Hata", "Tarih formatı YYYY-MM-DD olmalı.")
                return

            excl = {d for d, val in v_excl.items() if val.get()}
            try:
                self.constraints["default_duration"] = int(v_defdur.get())
                self.constraints["cooldown_min"] = int(v_cool.get())
            except ValueError:
                messagebox.showerror("Hata", "Süre/bekleme sayısal olmalı.")
                return

            self.constraints["date_start"] = d_start
            self.constraints["date_end"]   = d_end
            self.constraints["exclude_days"] = excl
            self.constraints["single_exam_at_a_time"] = bool(v_single.get())
            messagebox.showinfo("Kısıtlar", "Kısıtlar kaydedildi. Otomatik planlamayı tekrar çalıştırın.")
            top.destroy()

        ttk.Button(btns, text="Kaydet", command=_save).pack(side="right")

    # ----------------- OTURMA PLANI -----------------

    def open_seating(self):
        """
        PDF’te bu şekilde isteniyor: Seçili sınav için (ders+derslik+zaman),
        dersi alan öğrencileri dersliğin rows×cols×seats_per_desk kapasitesine
        sıra/sütun/koltuk index ile yerleştir ve PDF’e aktarılabilir ekran aç.
        """
        sel = self.tree.selection() if hasattr(self, "tree") else ()
        if not sel:
            messagebox.showwarning("Oturma Planı", "Lütfen önce listeden bir sınav seçin.")
            return

        item = self.tree.item(sel[0])
        values = item.get("values") or []
        if not values:
            messagebox.showwarning("Oturma Planı", "Seçim okunamadı.")
            return

        # Gizli ilk sütun: exam_id
        exam_id = values[0]

        from .seating_view import SeatingView
        top = tk.Toplevel(self)
        top.title("Oturma Planı")
        top.geometry("1000x600")
        SeatingView(top, exam_id=exam_id, user=getattr(self, "user", None)).pack(fill="both", expand=True)
