# src/ui/students_view.py
# Öğrenciyi numara/ad ile arar; seçilince aldığı dersleri listeler; Excel/İçe Aktarım penceresini açar.

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from core.db import get_conn


class StudentsView(ttk.Frame):
    """
    PDF’te bu şekilde isteniyor:
      - Sol: Öğrenci listesi (numara, ad soyad, sınıf)
      - Sağ: Seçili öğrencinin aldığı dersler (kod, ad, sınıf, hoca)
      - Üstte: arama (numara/ad)
      - İçe Aktarım: ayrı diyalogda (ImportView) açılır
    """
    def __init__(self, master, user=None, **kwargs):
        super().__init__(master, **kwargs)
        self.user = user or {}

        # ÜST BAR
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=8)

        ttk.Label(bar, text="Ara (no/ad):").pack(side="left")
        self.q = tk.StringVar()
        ttk.Entry(bar, textvariable=self.q, width=30).pack(side="left", padx=6)
        ttk.Button(bar, text="Ara", command=self.search).pack(side="left")
        ttk.Button(bar, text="Yenile", command=lambda: [self.q.set(""), self.search()]).pack(side="left", padx=6)
        ttk.Button(bar, text="İçe Aktar", command=self.open_import_dialog).pack(side="left", padx=6)

        # SOL/SAĞ BÖLME
        split = ttk.Panedwindow(self, orient="horizontal")
        split.pack(fill="both", expand=True, padx=10, pady=8)

        # SOL — Öğrenciler
        left = ttk.LabelFrame(split, text="Öğrenciler")
        split.add(left, weight=1)

        cols = ("id", "number", "full_name", "class_year")
        headers = ("ID", "Numara", "Ad Soyad", "Sınıf")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=16)
        for c, h in zip(cols, headers):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=120 if c != "full_name" else 220, anchor="center")

        # ID'yi gizle (tekil kimlik olarak tutuluyor)
        self.tree.column("id", width=0, minwidth=0, stretch=False)

        self.tree.bind("<<TreeviewSelect>>", lambda e: self.load_courses())
        self.tree.pack(fill="both", expand=True, padx=6, pady=6)

        # SAĞ — Aldığı Dersler
        right = ttk.LabelFrame(split, text="Aldığı Dersler")
        split.add(right, weight=1)

        ccols = ("code", "name", "class_year", "instructor")
        cheaders = ("Kod", "Ad", "Sınıf", "Hoca")
        self.ctree = ttk.Treeview(right, columns=ccols, show="headings", height=16)
        for c, h in zip(ccols, cheaders):
            self.ctree.heading(c, text=h)
            self.ctree.column(c, width=140 if c != "name" else 220, anchor="center")
        self.ctree.pack(fill="both", expand=True, padx=6, pady=6)

        # İlk veri
        self.search()

    # ---------- Yardımcılar ----------

    def _dept_clause(self, table_alias: str):
        """Kullanıcının bölümüne göre WHERE filtresi döndürür (admin ise filtre yok)."""
        role = (self.user or {}).get("role")
        dept_id = (self.user or {}).get("department_id")
        if role == "admin" or dept_id is None:
            return "", ()
        return f" AND {table_alias}.dept_id=?", (dept_id,)

    # ---------- Arama & Listeleme ----------

    def search(self):
        qtxt = f"%{self.q.get().strip()}%"
        where_dept, dept_params = self._dept_clause("s")

        sql = f"""
            SELECT s.id, s.number, s.full_name, s.class_year
            FROM students s
            WHERE (s.number LIKE ? OR s.full_name LIKE ?)
            {where_dept}
            ORDER BY s.class_year, s.number
        """
        params = (qtxt, qtxt, *dept_params)

        with get_conn() as con:
            cur = con.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()

        # Sol tabloyu doldur
        for i in self.tree.get_children():
            self.tree.delete(i)
        for r in rows:
            sid, num, name, yr = r
            self.tree.insert("", "end", values=(
                sid,
                "" if num is None else num,
                "" if name is None else name,
                "" if yr is None else yr
            ))

        # Sağ tabloyu temizle
        for i in self.ctree.get_children():
            self.ctree.delete(i)

        # İlk satırı seçip dersleri getir (varsa)
        kids = self.tree.get_children()
        if kids:
            first = kids[0]
            self.tree.selection_set(first)
            self.tree.focus(first)
            self.load_courses()

    def load_courses(self):
        sel = self.tree.selection()
        if not sel:
            f = self.tree.focus()
            if f:
                sel = (f,)
            else:
                return

        values = self.tree.item(sel[0], "values")
        if not values:
            return

        sid = values[0]  # gizli ID

        where_dept, dept_params = self._dept_clause("c")
        sql = f"""
            SELECT c.code, c.name, c.class_year, COALESCE(c.instructor,'')
            FROM enrollments e
            JOIN courses c ON c.id = e.course_id
            WHERE e.student_id=? {where_dept}
            ORDER BY c.class_year, c.code
        """
        params = (sid, *dept_params)

        with get_conn() as con:
            cur = con.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()

        # Sağ tabloyu doldur
        for i in self.ctree.get_children():
            self.ctree.delete(i)
        for r in rows:
            code, name, yr, inst = r
            self.ctree.insert("", "end", values=(
                "" if code is None else code,
                "" if name is None else name,
                "" if yr   is None else yr,
                "" if inst is None else inst
            ))

    # ---------- İçe Aktarım ----------

    def open_import_dialog(self):
        """ImportView'i diyalog olarak açar (Önizle → Sütun Eşle → Dry-Run → DB'ye Aktar)."""
        from ui.import_view import ImportView
        top = tk.Toplevel(self)
        top.title("Veri İçe Aktarımı")
        top.geometry("980x560")
        ImportView(top, user=self.user).pack(fill="both", expand=True)
