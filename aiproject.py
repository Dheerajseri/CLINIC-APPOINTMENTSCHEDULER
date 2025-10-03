#!/usr/bin/env python3
"""
Clinic Appointment Scheduler
Single-file implementation:
 - SQLite DB for doctors, patients, appointments
 - CSP-based scheduler (backtracking + forward checking + MRV)
 - Tkinter GUI to manage doctors/patients and run scheduler

Save as scheduler.py and run: python scheduler.py
"""

import sqlite3
import datetime
import json
import os
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

DB_PATH = "clinic.db"

# -------------------------
# Database helpers
# -------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    # doctors
    cur.execute("""
    CREATE TABLE IF NOT EXISTS doctors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        specialty TEXT,
        capacity_per_slot INTEGER DEFAULT 1
    )
    """)
    # patients
    cur.execute("""
    CREATE TABLE IF NOT EXISTS patients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        needs_specialist INTEGER DEFAULT 0,
        specialty_required TEXT,
        emergency INTEGER DEFAULT 0,
        preferred_slots TEXT
    )
    """)
    # appointments
    cur.execute("""
    CREATE TABLE IF NOT EXISTS appointments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER,
        doctor_id INTEGER,
        slot TEXT,
        date TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(patient_id) REFERENCES patients(id),
        FOREIGN KEY(doctor_id) REFERENCES doctors(id)
    )
    """)
    conn.commit()
    conn.close()

def add_doctor(name, specialty="", capacity=1):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO doctors (name, specialty, capacity_per_slot) VALUES (?, ?, ?)",
                (name, specialty, capacity))
    conn.commit()
    conn.close()

def add_patient(name, needs_specialist=0, specialty_required=None, emergency=0, preferred_slots=None):
    conn = get_conn()
    cur = conn.cursor()
    pref = ",".join(preferred_slots) if preferred_slots else None
    cur.execute("INSERT INTO patients (name, needs_specialist, specialty_required, emergency, preferred_slots) VALUES (?, ?, ?, ?, ?)",
                (name, needs_specialist, specialty_required, emergency, pref))
    conn.commit()
    conn.close()

def list_doctors():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM doctors ORDER BY id")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def list_patients():
    conn = get_conn()
    cur = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM patients ORDER BY id")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def save_appointment(patient_id, doctor_id, slot, date):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO appointments (patient_id, doctor_id, slot, date) VALUES (?, ?, ?, ?)",
                (patient_id, doctor_id, slot, date))
    conn.commit()
    conn.close()

def list_appointments(date=None):
    conn = get_conn()
    cur = conn.cursor()
    if date:
        cur.execute("""SELECT a.id, a.patient_id, p.name as patient_name, a.doctor_id, d.name as doctor_name, a.slot, a.date
                       FROM appointments a
                       LEFT JOIN patients p ON p.id = a.patient_id
                       LEFT JOIN doctors d ON d.id = a.doctor_id
                       WHERE a.date = ?
                       ORDER BY a.slot""", (date,))
    else:
        cur.execute("""SELECT a.id, a.patient_id, p.name as patient_name, a.doctor_id, d.name as doctor_name, a.slot, a.date
                       FROM appointments a
                       LEFT JOIN patients p ON p.id = a.patient_id
                       LEFT JOIN doctors d ON d.id = a.doctor_id
                       ORDER BY a.date, a.slot""")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def clear_appointments_for_date(date):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM appointments WHERE date = ?", (date,))
    conn.commit()
    conn.close()

# -------------------------
# Time slot generation
# -------------------------
def generate_slots(start="09:00", end="17:00", slot_minutes=30):
    """Return list of slot strings 'HH:MM'."""
    fmt = "%H:%M"
    s = datetime.datetime.strptime(start, fmt)
    e = datetime.datetime.strptime(end, fmt)
    slots = []
    cur = s
    while cur < e:
        slots.append(cur.strftime(fmt))
        cur += datetime.timedelta(minutes=slot_minutes)
    return slots

# -------------------------
# CSP scheduler
# -------------------------
def build_domains(patients, doctors, slots):
    """
    Build initial domains:
      - For each patient, a list of (doctor_id, slot) pairs allowed.
      - Respect specialist requirement & preferred slots if present.
    """
    doctor_by_id = {d['id']: d for d in doctors}
    domains = {}
    for p in patients:
        allowed = []
        pref_list = []
        if p.get('preferred_slots'):
            # stored as comma separated string in DB
            pref_list = [s.strip() for s in (p['preferred_slots'] or "").split(",") if s.strip()]
        for d in doctors:
            # if patient needs specialist and doctor doesn't match, skip
            if p['needs_specialist'] and p['specialty_required']:
                if not d['specialty'] or d['specialty'].lower() != p['specialty_required'].lower():
                    continue
            # for each slot
            for slot in slots:
                if pref_list and slot not in pref_list:
                    continue
                allowed.append((d['id'], slot))
        domains[p['id']] = allowed
    return domains

def select_unassigned_var(domains, assigned, patient_meta):
    """
    MRV heuristic, with emergency priority:
        - Prefer emergency patients first (among unassigned)
        - Then pick variable with minimum remaining values.
    patient_meta: dict patient_id -> {'emergency': 0/1}
    """
    unassigned = [vid for vid in domains.keys() if vid not in assigned]
    # emergency first
    emergencies = [v for v in unassigned if patient_meta.get(v, {}).get('emergency',0)]
    pool = emergencies if emergencies else unassigned
    # MRV
    best = None
    best_len = None
    for v in pool:
        l = len(domains[v])
        if best is None or l < best_len:
            best = v
            best_len = l
    return best

def backtracking_search(domains, doctor_capacity, patient_meta):
    """
    domains: dict var -> list of (doctor_id, slot)
    doctor_capacity: dict doctor_id -> capacity_per_slot (int)
    patient_meta: dict patient_id -> meta (emergency etc)
    """

    assigned = {}
    capacity_map = {}  # (doctor_id, slot) -> count

    # recursive function
    def backtrack():
        if len(assigned) == len(domains):
            return dict(assigned)  # found complete

        var = select_unassigned_var(domains, assigned, patient_meta)
        if var is None:
            return None

        domain_vals = list(domains[var])  # value ordering: try earlier slots first (stable)
        # sort by slot time ascending to give earlier slots priority
        domain_vals.sort(key=lambda x: x[1])

        for val in domain_vals:
            doc_id, slot = val
            cap_key = (doc_id, slot)
            used = capacity_map.get(cap_key, 0)
            cap_limit = doctor_capacity.get(doc_id, 1)
            if used >= cap_limit:
                continue  # can't place here

            # Tentatively assign
            assigned[var] = val
            capacity_map[cap_key] = used + 1

            # Forward checking: reduce domains of other unassigned variables
            removed_snapshot = {}
            failure = False
            for other in domains:
                if other in assigned:
                    continue
                new_domain = []
                removed_snapshot[other] = []
                for v in domains[other]:
                    d_id, s = v
                    key = (d_id, s)
                    cur_used = capacity_map.get(key, 0)
                    if cur_used >= doctor_capacity.get(d_id, 1):
                        # this value violates capacity now; skip (remove)
                        removed_snapshot[other].append(v)
                        continue
                    new_domain.append(v)
                if not new_domain:
                    failure = True
                    break
                # replace domain
                domains[other] = new_domain

            if not failure:
                result = backtrack()
                if result is not None:
                    return result

            # undo assignment & restore domains & capacity map
            del assigned[var]
            capacity_map[cap_key] = capacity_map.get(cap_key, 1) - 1
            if capacity_map[cap_key] == 0:
                del capacity_map[cap_key]
            # restore domains from snapshot
            for other, removed in removed_snapshot.items():
                if removed:
                    # put removed values back (append and sort to keep stable)
                    domains[other].extend(removed)
                    domains[other].sort(key=lambda x: x[1])

        return None

    # Make a deep copy of domains to allow safe modifications
    domains = {k: list(v) for k,v in domains.items()}
    return backtrack()

def schedule_for_date(date_str, start="09:00", end="17:00", slot_minutes=30, clear_existing=True):
    """
    Main interface: fetch DB, run CSP, write appointments into DB for given date_str (YYYY-MM-DD).
    Returns tuple (success_boolean, assignments or None, message)
    """
    doctors = list_doctors()
    patients = list_patients()
    if not doctors:
        return False, None, "No doctors registered."
    if not patients:
        return False, None, "No patients registered."

    slots = generate_slots(start, end, slot_minutes)
    domains = build_domains(patients, doctors, slots)

    # patient metadata
    patient_meta = {p['id']: {'emergency': p['emergency'], 'name': p['name']} for p in patients}

    doctor_capacity = {d['id']: int(d.get('capacity_per_slot', 1)) for d in doctors}

    # Clear existing appointments for that date if desired
    if clear_existing:
        clear_appointments_for_date(date_str)

    assignment = backtracking_search(domains, doctor_capacity, patient_meta)
    if assignment is None:
        return False, None, "Could not find feasible schedule with current constraints."

    # Save assignments to DB
    for patient_id, (doctor_id, slot) in assignment.items():
        save_appointment(patient_id, doctor_id, slot, date_str)

    # build readable list
    readable = []
    # convert IDs to names for display
    doc_names = {d['id']: d['name'] for d in doctors}
    pat_names = {p['id']: p['name'] for p in patients}
    for pid, (did, slot) in assignment.items():
        readable.append({
            'patient_id': pid, 'patient_name': pat_names.get(pid, 'Unknown'),
            'doctor_id': did, 'doctor_name': doc_names.get(did, 'Unknown'),
            'slot': slot, 'date': date_str
        })
    # sort by slot
    readable.sort(key=lambda x: x['slot'])
    return True, readable, f"Scheduled {len(readable)} patients for {date_str}"

# -------------------------
# Sample data helper
# -------------------------
def populate_sample_data():
    # add sample doctors and patients if empty
    if list_doctors():
        return
    add_doctor("Dr. Alice", "Cardiology", capacity=1)
    add_doctor("Dr. Bob", "General", capacity=2)
    add_doctor("Dr. Carol", "Dermatology", capacity=1)
    # patients
    add_patient("John Doe", needs_specialist=0, specialty_required=None, emergency=1, preferred_slots=None)
    add_patient("Jane Roe", needs_specialist=1, specialty_required="Cardiology", emergency=0)
    add_patient("Charlie P", needs_specialist=0, specialty_required=None, emergency=0)
    add_patient("Eve Q", needs_specialist=1, specialty_required="Dermatology", emergency=0)
    add_patient("Mallory X", needs_specialist=0, emergency=0)

# -------------------------
# Tkinter GUI
# -------------------------
class SchedulerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Clinic Appointment Scheduler (CSP)")
        self.geometry("1000x600")
        self.create_widgets()
        self.refresh_lists()

    def create_widgets(self):
        # Top controls
        top = ttk.Frame(self)
        top.pack(side=tk.TOP, fill=tk.X, padx=8, pady=8)

        ttk.Label(top, text="Schedule Date (YYYY-MM-DD):").pack(side=tk.LEFT)
        self.date_var = tk.StringVar()
        self.date_var.set(datetime.datetime.today().strftime("%Y-%m-%d"))
        ttk.Entry(top, textvariable=self.date_var, width=12).pack(side=tk.LEFT, padx=6)

        ttk.Button(top, text="Populate sample data", command=self.on_populate).pack(side=tk.LEFT, padx=6)
        ttk.Button(top, text="Run Scheduler", command=self.on_run_scheduler).pack(side=tk.LEFT, padx=6)
        ttk.Button(top, text="View Appointments", command=self.on_view_appointments).pack(side=tk.LEFT, padx=6)
        ttk.Button(top, text="Clear Appointments (date)", command=self.on_clear_appointments).pack(side=tk.LEFT, padx=6)

        main = ttk.Frame(self)
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # Left panel: doctors & patients
        left = ttk.Frame(main)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=6)

        # Doctors
        ttk.Label(left, text="Doctors").pack(anchor=tk.W)
        self.doctor_tree = ttk.Treeview(left, columns=("id","name","spec","cap"), show="headings", height=8)
        self.doctor_tree.heading("id", text="ID")
        self.doctor_tree.heading("name", text="Name")
        self.doctor_tree.heading("spec", text="Specialty")
        self.doctor_tree.heading("cap", text="Cap/slot")
        self.doctor_tree.pack()
        ttk.Button(left, text="Add Doctor", command=self.on_add_doctor).pack(pady=4)

        # Patients
        ttk.Label(left, text="Patients").pack(anchor=tk.W, pady=(12,0))
        self.patient_tree = ttk.Treeview(left, columns=("id","name","spec_needed","spec","emergency"), show="headings", height=10)
        self.patient_tree.heading("id", text="ID")
        self.patient_tree.heading("name", text="Name")
        self.patient_tree.heading("spec_needed", text="Needs Specialist")
        self.patient_tree.heading("spec", text="Specialty Required")
        self.patient_tree.heading("emergency", text="Emergency")
        self.patient_tree.pack()
        ttk.Button(left, text="Add Patient", command=self.on_add_patient).pack(pady=4)

        # Right panel: schedule view
        right = ttk.Frame(main)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=6, pady=6)
        ttk.Label(right, text="Schedule / Appointments").pack(anchor=tk.W)
        self.schedule_tree = ttk.Treeview(right, columns=("slot","patient","doctor"), show="headings")
        self.schedule_tree.heading("slot", text="Slot")
        self.schedule_tree.heading("patient", text="Patient")
        self.schedule_tree.heading("doctor", text="Doctor")
        self.schedule_tree.pack(fill=tk.BOTH, expand=True)

    def refresh_lists(self):
        # doctors
        for r in self.doctor_tree.get_children():
            self.doctor_tree.delete(r)
        for d in list_doctors():
            self.doctor_tree.insert("", tk.END, values=(d['id'], d['name'], d['specialty'] or "", d['capacity_per_slot']))
        # patients
        for r in self.patient_tree.get_children():
            self.patient_tree.delete(r)
        for p in list_patients():
            self.patient_tree.insert("", tk.END, values=(p['id'], p['name'], "Yes" if p['needs_specialist'] else "No", p.get('specialty_required') or "", "Yes" if p['emergency'] else "No"))

    def on_populate(self):
        populate_sample_data()
        self.refresh_lists()
        messagebox.showinfo("Sample Data", "Sample doctors and patients added (if DB was empty).")

    def on_add_doctor(self):
        name = simpledialog.askstring("Doctor name", "Enter doctor's name:")
        if not name:
            return
        specialty = simpledialog.askstring("Specialty", "Enter specialty (or leave blank):")
        cap = simpledialog.askinteger("Capacity per slot", "How many patients per slot can this doctor handle?", initialvalue=1, minvalue=1)
        add_doctor(name, specialty or "", cap or 1)
        self.refresh_lists()

    def on_add_patient(self):
        name = simpledialog.askstring("Patient name", "Enter patient's name:")
        if not name:
            return
        needs_spec = messagebox.askyesno("Needs specialist?", "Does this patient require a specialist?")
        spec_required = None
        if needs_spec:
            spec_required = simpledialog.askstring("Specialty required", "Enter required specialty:")
        emergency = messagebox.askyesno("Emergency?", "Is this an emergency case?")
        # optional preferred slots
        pref = simpledialog.askstring("Preferred slots (optional)", "Enter preferred slots as comma-separated times (e.g. 09:00,09:30) or leave blank:")
        pref_list = [s.strip() for s in (pref or "").split(",") if s.strip()] if pref else None
        add_patient(name, 1 if needs_spec else 0, spec_required, 1 if emergency else 0, pref_list)
        self.refresh_lists()

    def on_run_scheduler(self):
        date = self.date_var.get().strip()
        try:
            datetime.datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            messagebox.showerror("Date error", "Date must be YYYY-MM-DD")
            return
        ok, readable, msg = schedule_for_date(date)
        if ok:
            # display
            for r in self.schedule_tree.get_children():
                self.schedule_tree.delete(r)
            for entry in readable:
                self.schedule_tree.insert("", tk.END, values=(entry['slot'], entry['patient_name'], entry['doctor_name']))
            messagebox.showinfo("Scheduling successful", msg)
        else:
            messagebox.showwarning("Scheduling failed", msg)

    def on_view_appointments(self):
        date = self.date_var.get().strip()
        try:
            datetime.datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            messagebox.showerror("Date error", "Date must be YYYY-MM-DD")
            return
        apps = list_appointments(date)
        for r in self.schedule_tree.get_children():
            self.schedule_tree.delete(r)
        for a in apps:
            self.schedule_tree.insert("", tk.END, values=(a['slot'], a['patient_name'], a['doctor_name']))
        messagebox.showinfo("Appointments loaded", f"{len(apps)} appointments on {date}.")

    def on_clear_appointments(self):
        date = self.date_var.get().strip()
        if messagebox.askyesno("Confirm", f"Delete all appointments for {date}?"):
            clear_appointments_for_date(date)
            for r in self.schedule_tree.get_children():
                self.schedule_tree.delete(r)
            messagebox.showinfo("Cleared", f"Appointments for {date} cleared.")

if __name__ == "__main__":
    # initialize db
    init_db()
    # Launch GUI
    app = SchedulerApp()
    app.mainloop()
