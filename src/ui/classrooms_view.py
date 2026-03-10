import tkinter as tk
from tkinter import ttk, messagebox
from core.db import get_conn

class ClassroomsView(ttk.Frame):
    """
    PDF’te bu şekilde isteniyor:
      - Kapasite kullanıcıdan alınmaz; rows × cols × seats_per_desk otomatik hesaplanır.
      - ID ile arama çubuğu (sadece ID).
      - 2’li/3’lü sıra seçimi (seats_per_desk).
      - Bölüm filtresi: admin tümünü görür, koordinatör sadece kendi bölümünü.
      - Önizleme kanvasında basit yerleşim.
    """
    def __init__(self, master, user, **kwargs):
        super().__init__(master, **kwargs)
        self.user = user
        self.search_id = tk.StringVar()  # ID ile arama

        # ---------- Form ----------
        frm = ttk.LabelFrame(self, text="Derslik Ekle")
        frm.pack(side="top", fill="x", padx=10, pady=10)

        self.code_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.rows_var = tk.StringVar()
        self.cols_var = tk.StringVar()
        self.seats_var = tk.StringVar(value="2")
        self._cap_text = tk.StringVar(value="Kapasite: 0")  # dinamik gösterim

        r = 0
        ttk.Label(frm, text="Kod").grid(row=r, column=0, sticky="e", padx=6, pady=4)
        ttk.Entry(frm, textvariable=self.code_var, width=18).grid(row=r, column=1, padx=6, pady=4)

        ttk.Label(frm, text="Ad").grid(row=r, column=2, sticky="e", padx=6, pady=4)
        ttk.Entry(frm, textvariable=self.name_var, width=26).grid(row=r, column=3, padx=6, pady=4)

        r += 1
        ttk.Label(frm, text="Satır (rows)").grid(row=r, column=0, sticky="e", padx=6, pady=4)
        e_rows = ttk.Entry(frm, textvariable=self.rows_var, width=10)
        e_rows.grid(row=r, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(frm, text="Sütun (cols)").grid(row=r, column=2, sticky="e", padx=6, pady=4)
        e_cols = ttk.Entry(frm, textvariable=self.cols_var, width=10)
        e_cols.grid(row=r, column=3, sticky="w", padx=6, pady=4)

        r += 1
        ttk.Label(frm, text="Sıra Tipi").grid(row=r, column=0, sticky="e", padx=6, pady=4)
        seats_box = ttk.Combobox(frm, textvariable=self.seats_var, values=["2","3"], width=8, state="readonly")
        seats_box.grid(row=r, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(frm, textvariable=self._cap_text, foreground="#444").grid(row=r, column=2, columnspan=1, sticky="e", padx=6)
        ttk.Button(frm, text="Ekle", command=self.add_classroom).grid(row=r, column=3, padx=10, sticky="w")

        for c in range(5):
            frm.columnconfigure(c, weight=1)

        # rows/cols/seats değiştikçe kapasiteyi canlı güncelle
        def _recalc(_=None):
            try:
                r = int(self.rows_var.get() or 0)
                c = int(self.cols_var.get() or 0)
                s = int(self.seats_var.get() or 0)
                cap = max(0, r * c * s)
                self._cap_text.set(f"Kapasite: {cap}")
            except Exception:
                self._cap_text.set("Kapasite: ?")
        for v in (self.rows_var, self.cols_var, self.seats_var):
            v.trace_add("write", _recalc)

        # ---------- Liste ----------
        list_frame = ttk.LabelFrame(self, text="Derslikler")
        list_frame.pack(side="left", fill="both", expand=True, padx=(10,5), pady=10)

        # ID ile arama barı
        search_bar = ttk.Frame(list_frame)
        search_bar.pack(fill="x", padx=6, pady=(6, 0))
        ttk.Label(search_bar, text="Sınıf ID ile ara:").pack(side="left")
        ttk.Entry(search_bar, textvariable=self.search_id, width=12).pack(side="left", padx=6)
        ttk.Button(search_bar, text="Ara", command=self.refresh).pack(side="left")
        ttk.Button(search_bar, text="Temizle", command=lambda: (self.search_id.set(""), self.refresh())).pack(side="left", padx=(6, 0))

        cols = ("id","code","name","capacity","rows","cols","seats")
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings", height=12)
        for c, text in zip(cols, ("ID","Kod","Ad","Kapasite","Rows","Cols","Sıra Tipi")):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=80 if c in ("id","rows","cols","seats") else 140, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=6, pady=6)

        btns = ttk.Frame(list_frame)
        btns.pack(fill="x", padx=6, pady=(0,6))
        ttk.Button(btns, text="Yenile", command=self.refresh).pack(side="left")
        ttk.Button(btns, text="Sil (seçili)", command=self.delete_selected).pack(side="left", padx=6)

        # ---------- Görselleştirme ----------
        vis_frame = ttk.LabelFrame(self, text="Oturma Düzeni (Önizleme)")
        vis_frame.pack(side="right", fill="both", expand=True, padx=(5,10), pady=10)

        self.canvas = tk.Canvas(vis_frame, width=420, height=320, background="#fafafa")
        self.canvas.pack(fill="both", expand=True, padx=6, pady=6)
        ttk.Button(vis_frame, text="Seçiliyi Görselleştir", command=self.visualize_selected).pack(pady=(0,8))

        self.refresh()

    # ----- DB İşlemleri -----

    def add_classroom(self):
        code = (self.code_var.get() or "").strip()
        name = (self.name_var.get() or "").strip()
        rows = (self.rows_var.get() or "").strip()
        cols = (self.cols_var.get() or "").strip()
        seats = (self.seats_var.get() or "").strip()

        if not (code and name and rows and cols and seats):
            messagebox.showwarning("Uyarı", "Kod, Ad, rows, cols ve sıra tipi zorunludur.")
            return
        try:
            rows_i = int(rows); cols_i = int(cols); seats_i = int(seats)
            if rows_i <= 0 or cols_i <= 0 or seats_i <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Hata", "rows/cols/sıra tipi pozitif tam sayı olmalıdır.")
            return

        capacity = rows_i * cols_i * seats_i  # otomatik hesap
        dept_id = self.user.get("department_id") or 1

        try:
            with get_conn() as con:
                cur = con.cursor()
                # aynı bölümde aynı kod varsa engelle (temel bütünlük)
                cur.execute("SELECT 1 FROM classrooms WHERE dept_id=? AND code=?", (dept_id, code))
                if cur.fetchone():
                    messagebox.showerror("Hata", f"Bu bölümde {code} kodlu derslik zaten var.")
                    return

                cur.execute("""
                    INSERT INTO classrooms(dept_id, code, name, capacity, rows, cols, seats_per_desk)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (dept_id, code, name, capacity, rows_i, cols_i, seats_i))
            self._clear_form()
            self.refresh()
            messagebox.showinfo("Başarılı", "Derslik eklendi.")
        except Exception as e:
            messagebox.showerror("Hata", f"Kayıt eklenemedi: {e}")

    def _clear_form(self):
        self.code_var.set("")
        self.name_var.set("")
        self.rows_var.set("")
        self.cols_var.set("")
        self.seats_var.set("2")
        self._cap_text.set("Kapasite: 0")

    def refresh(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        with get_conn() as con:
            cur = con.cursor()
            dept_id = self.user.get("department_id")
            q = (self.search_id.get() or "").strip()

            if q:
                # ID ile doğrudan ara (rol fark etmez)
                cur.execute("""SELECT id, code, name, capacity, rows, cols, seats_per_desk
                               FROM classrooms WHERE id=?""", (q,))
                rows = cur.fetchall()
            else:
                # rol/bölüm filtresi
                if self.user.get("role") == "admin" or not dept_id:
                    cur.execute("""SELECT id, code, name, capacity, rows, cols, seats_per_desk
                                   FROM classrooms ORDER BY dept_id, code""")
                else:
                    cur.execute("""SELECT id, code, name, capacity, rows, cols, seats_per_desk
                                   FROM classrooms WHERE dept_id=? ORDER BY code""", (dept_id,))
                rows = cur.fetchall()

        for r in rows:
            self.tree.insert("", "end", values=r)

    def delete_selected(self):
        item = self.tree.selection()
        if not item:
            messagebox.showwarning("Uyarı", "Silmek için listeden bir satır seçin.")
            return
        cid = self.tree.item(item[0], "values")[0]
        if not messagebox.askyesno("Onay", f"ID={cid} derslik silinsin mi?"):
            return
        try:
            with get_conn() as con:
                cur = con.cursor()
                cur.execute("DELETE FROM classrooms WHERE id=?", (cid,))
            self.refresh()
        except Exception as e:
            messagebox.showerror("Hata", f"Silinemedi: {e}")

    # ----- Görselleştirme -----

    def visualize_selected(self):
        item = self.tree.selection()
        if not item:
            messagebox.showwarning("Uyarı", "Önizleme için listeden bir derslik seçin.")
            return
        _, code, name, capacity, rows, cols, seats = self.tree.item(item[0], "values")
        try:
            self.draw_layout(int(rows), int(cols), int(seats),
                             title=f"{code} - {name}  (kap: {capacity})")
        except Exception as e:
            messagebox.showerror("Önizleme", f"Çizim sırasında hata: {e}")

    def draw_layout(self, rows, cols, seats, title=""):
        # Canvas ölçülerinin güncel olduğundan emin ol
        self.canvas.update_idletasks()
        self.canvas.delete("all")
        pad = 12
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        # hücre boyutları
        desk_w = max(24, int((cw - 2*pad) / max(cols,1)) - 8)
        desk_h = max(20, int((ch - 2*pad) / max(rows,1)) - 8)

        # Başlık
        self.canvas.create_text(10, 12, anchor="nw", text=title, font=("Segoe UI", 9, "bold"))

        y = pad + 18
        for r in range(rows):
            x = pad
            for c in range(cols):
                self.canvas.create_rectangle(x, y, x+desk_w, y+desk_h, outline="#666")
                # Sıra tipini göstermek için küçük sayı
                self.canvas.create_text(x+desk_w/2, y+desk_h/2, text=f"{seats}", font=("Segoe UI", 9))
                x += desk_w + 8
            y += desk_h + 8
