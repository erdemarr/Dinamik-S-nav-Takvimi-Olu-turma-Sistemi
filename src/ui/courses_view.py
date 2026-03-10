# src/ui/courses_view.py
# Dersleri listeler; seçilince dersi alan öğrencileri gösterir (bilgi etiketi + CSV dışa aktarım)

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import csv
from core.db import get_conn


class CoursesView(ttk.Frame):
    """
    PDF’te bu şekilde isteniyor:
      - Sol: Ders listesi
      - Sağ: Seçili dersi alan öğrenciler
      - Üstte: ders bilgisi (kod, ad, sınıf, öğrenci sayısı, zorunlu/ seçmeli, hoca)
      - CSV dışa aktarım (sağdaki öğrenci listesi)
    """
    def __init__(self, master, user, **kwargs):
        super().__init__(master, **kwargs)
        self.user = user

        # ÜST BAR — arama (kod/ad)
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=8)

        ttk.Label(bar, text="Ara (kod/ad):").pack(side="left")
        self.q = tk.StringVar()
        ttk.Entry(bar, textvariable=self.q, width=30).pack(side="left", padx=6)
        ttk.Button(bar, text="Ara", command=self.refresh).pack(side="left")
        ttk.Button(bar, text="Yenile", command=lambda: [self.q.set(""), self.refresh()]).pack(side="left", padx=6)

        # İÇERİK — sol/sağ paneller
        content = ttk.Frame(self)
        content.pack(fill="both", expand=True, padx=10, pady=(2, 10))

        # SOL — Ders listesi
        left = ttk.LabelFrame(content, text="Dersler")
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))

        cols = ("id", "code", "name", "class_year", "is_compulsory", "instructor")
        headers = ("ID", "Kod", "Ad", "Sınıf", "Zorunlu(1/0)", "Hoca")

        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=16)
        for c, h in zip(cols, headers):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=110 if c != "name" else 220, anchor="center")

        # ID sütununu görünmez yap (tekil kimlik için saklı)
        self.tree.column("id", width=0, minwidth=0, stretch=False)

        self.tree.bind("<<TreeviewSelect>>", lambda e: self.load_students())
        self.tree.pack(fill="both", expand=True, padx=6, pady=6)

        # SAĞ — Dersi alan öğrenciler
        right = ttk.LabelFrame(content, text="Dersi Alan Öğrenciler")
        right.pack(side="right", fill="both", expand=True, padx=(6, 0))

        info_bar = ttk.Frame(right)
        info_bar.pack(fill="x", padx=6, pady=(6, 0))

        # Seçili ders bilgisi etiketi
        self.info = ttk.Label(info_bar, text="Bir ders seçiniz.", foreground="#444")
        self.info.pack(side="left")

        ttk.Button(info_bar, text="Dışa Aktar (CSV)", command=self.export_csv).pack(side="right")

        scols = ("number", "full_name", "class_year")
        sheaders = ("Numara", "Ad Soyad", "Sınıf")
        self.stree = ttk.Treeview(right, columns=scols, show="headings", height=16)
        for c, h in zip(scols, sheaders):
            self.stree.heading(c, text=h)
            self.stree.column(c, width=160 if c != "full_name" else 220, anchor="center")
        self.stree.pack(fill="both", expand=True, padx=6, pady=6)

        self.refresh()

    # --------- Veri yükleme ---------

    def refresh(self):
        qtxt_like = f"%{self.q.get().strip()}%"
        where_dept, dept_params = self._dept_clause("c")

        # Not: Arama hem kod hem ada uygulanır
        sql = f"""
            SELECT
                c.id,
                c.code,
                c.name,
                c.class_year,
                COALESCE(c.is_compulsory, 0) AS is_compulsory,
                COALESCE(c.instructor, '')  AS instructor
            FROM courses c
            WHERE (c.code LIKE ? OR c.name LIKE ?)
            {where_dept}
            ORDER BY c.class_year, c.code
        """
        params = (qtxt_like, qtxt_like, *dept_params)

        with get_conn() as con:
            cur = con.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()

        # Sol tabloyu doldur
        for i in self.tree.get_children():
            self.tree.delete(i)
        for r in rows:
            self.tree.insert("", "end", values=r)

        # Sağ paneli temizle
        for i in self.stree.get_children():
            self.stree.delete(i)
        self.info.config(text="Bir ders seçiniz.", foreground="#444")

    def load_students(self):
        """Seçili dersin öğrencilerini sağ tarafa yükler ve üstte bilgi etiketini günceller."""
        sel = self.tree.selection()
        if not sel:
            return

        values = self.tree.item(sel[0], "values")
        if not values:
            return

        # Sütun sırası: (id, code, name, class_year, is_compulsory, instructor)
        cid, code, name, class_year, is_compulsory, instructor = values

        with get_conn() as con:
            cur = con.cursor()
            cur.execute("""
                SELECT s.number, s.full_name, s.class_year
                FROM enrollments e
                JOIN students s ON s.id = e.student_id
                WHERE e.course_id=?
                ORDER BY s.class_year, s.number
            """, (cid,))
            rows = cur.fetchall()

        # Sağ tabloyu doldur
        for i in self.stree.get_children():
            self.stree.delete(i)
        for r in rows:
            # None güvenliği
            num = r[0] if r[0] is not None else ""
            ful = r[1] if r[1] is not None else ""
            yr  = r[2] if r[2] is not None else ""
            self.stree.insert("", "end", values=(num, ful, yr))

        # Bilgi etiketi
        count = len(rows)
        z_text = "Zorunlu" if str(is_compulsory) == "1" else "Seçmeli"
        instr_text = f" • Hoca: {instructor}" if instructor else ""
        self.info.config(
            text=f"{code} — {name} • Sınıf: {class_year} • Öğrenci: {count} • {z_text}{instr_text}",
            foreground="#222"
        )

    # --------- CSV dışa aktarım ---------

    def export_csv(self):
        """Sağdaki öğrenci listesini CSV olarak dışa aktarır."""
        rows = [self.stree.item(it, "values") for it in self.stree.get_children()]
        if not rows:
            messagebox.showinfo("Bilgi", "Dışa aktarılacak öğrenci bulunmuyor. Önce bir ders seçin.")
            return

        path = filedialog.asksaveasfilename(
            title="CSV olarak kaydet",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")]
        )
        if not path:
            return

        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["number", "full_name", "class_year"])
                for r in rows:
                    writer.writerow(r)
            messagebox.showinfo("Tamam", "Liste CSV olarak kaydedildi.")
        except Exception as e:
            messagebox.showerror("Hata", f"Kaydetme sırasında hata oluştu:\n{e}")

    # --------- Bölüm filtresi ---------

    def _dept_clause(self, table_alias: str):
        """
        admin ise: filtre yok → ("", ())
        koordinator ise: AND {alias}.dept_id=? → (" AND {alias}.dept_id=?", (dept_id,))
        """
        role = (self.user or {}).get("role")
        dept_id = (self.user or {}).get("department_id")
        if role == "admin" or dept_id is None:
            return "", ()
        return f" AND {table_alias}.dept_id=?", (dept_id,)
