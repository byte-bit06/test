from calendar_client import GoogleCalendarClient
from task_scheduler import TaskScheduler, Task, WorkSchedule
from ai_calendar_optimizer import AICalendarOptimizer, CapacityWarning, Bottleneck, PriorityAnalysis
from datetime import datetime, timedelta, timezone, date as dt_date, time as dt_time
from typing import Dict
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import random
import threading


def to_utc_iso(dt_local: datetime) -> str:
    if dt_local.tzinfo is None:
        dt_local = dt_local.astimezone()
    return dt_local.astimezone(timezone.utc).isoformat()


def parse_time_hhmm(text: str) -> tuple[int, int]:
    text = (text or "").strip()
    try:
        parts = text.split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        h = max(0, min(23, h))
        m = max(0, min(59, m))
        return h, m
    except Exception:
        return 9, 0


# Modern color palette (Google Calendar inspired)
EVENT_COLORS = [
    "#039BE5", "#7986CB", "#33B679", "#8E24AA", "#E67C73",
    "#F6BF26", "#F4511E", "#616161", "#3F51B5", "#0B8043"
]


class CalendarApp:
    def __init__(self, root: tk.Tk, ai_api_key: str = None):
        self.root = root
        self.root.title("AI Calendar - Motion-Inspired")
        self.client = GoogleCalendarClient()
        self.scheduler = TaskScheduler(self.client)
        
        # Initialize AI Calendar Optimizer (async for Hugging Face models)
        import os
        use_hf = os.getenv("USE_HUGGINGFACE", "false").lower() == "true"
        # Default to TinyLlama - small, fast, good for structured tasks
        hf_model = os.getenv("HF_MODEL_NAME", "TinyLlama/TinyLlama-1.1B-Chat-v1.0")
        
        # Initialize optimizer without AI client first (loads UI immediately)
        self.optimizer = AICalendarOptimizer(
            self.client, 
            self.scheduler, 
            ai_client=None,  # Will be loaded async
            api_key=ai_api_key,
            use_huggingface=use_hf,
            hf_model=hf_model
        )
        self.optimizer.auto_replan_enabled = True
        self.optimizer.replan_on_event_change = True
        
        # Load AI client asynchronously (non-blocking)
        self._load_ai_client_async(use_hf, hf_model, ai_api_key)
        
        self.today_date = datetime.now().date()
        self.week_start = self.today_date - timedelta(days=self.today_date.weekday())
        self.events = []
        self.render_boxes = []
        self.selected_event_id = None
        self.drag_start = None
        self.drag_day_idx = None
        self.top_priority_task = None
        self.capacity_warnings = []
        self.bottlenecks = []
        
        # Auto-optimization thread
        self.auto_optimize_running = False
        
        # Loading dialog (for async model loading)
        self._loading_dialog = None
        
        # Prevent recursive replanning
        self._is_replanning = False
        self._last_replan_time = None
        self._replan_cooldown = 5  # Minimum seconds between replans
        
        self._build_ui()
        self.refresh_events()
        self._start_continuous_optimization()

    def _build_ui(self):
        self.root.geometry("1400x800")
        self.root.configure(bg="#ffffff")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        # Modern header with clean design
        header = tk.Frame(self.root, bg="#ffffff", height=70)
        header.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        header.grid_propagate(False)
        header.columnconfigure(4, weight=1)

        # Navigation buttons with modern style
        nav_frame = tk.Frame(header, bg="#ffffff")
        nav_frame.grid(row=0, column=0, sticky="w", padx=20, pady=15)

        self._create_nav_button(nav_frame, "◀", self.prev_week).pack(side="left", padx=2)
        self._create_nav_button(nav_frame, "Today", self.go_today).pack(side="left", padx=8)
        self._create_nav_button(nav_frame, "▶", self.next_week).pack(side="left", padx=2)

        # Date range label
        self.date_label = tk.Label(header, text="", font=("Segoe UI", 16, "bold"),
                                   bg="#ffffff", fg="#202124")
        self.date_label.grid(row=0, column=1, sticky="w", padx=20)

        # User info and action buttons
        action_frame = tk.Frame(header, bg="#ffffff")
        action_frame.grid(row=0, column=5, sticky="e", padx=20, pady=15)

        # User email label
        self.user_label = tk.Label(action_frame, text="", bg="#ffffff", fg="#5f6368",
                                   font=("Segoe UI", 9))
        self.user_label.pack(side="left", padx=(0, 12))
        self._update_user_label()

        # Account menu button
        account_btn = self._create_action_button(action_frame, "👤 Account", self._show_account_menu)
        account_btn.pack(side="left", padx=4)

        self._create_action_button(action_frame, "📋 Tasks", self._show_task_manager).pack(side="left", padx=4)
        self._create_action_button(action_frame, "🔄 Refresh", self.refresh_events).pack(side="left", padx=4)
        self._create_action_button(action_frame, "🔀 Shuffle Tasks", self._shuffle_tasks).pack(side="left", padx=4)
        self._create_action_button(action_frame, "🤖 Optimize", self._optimize_schedule).pack(side="left", padx=4)
        self._create_action_button(action_frame, "📊 Health", self._show_health_report).pack(side="left", padx=4)
        self._create_primary_button(action_frame, "+ Create", self.add_event_dialog).pack(side="left", padx=4)
        
        # Priority indicator (will be updated dynamically)
        self.priority_indicator = tk.Label(action_frame, text="", bg="#ffffff", fg="#ea4335",
                                          font=("Segoe UI", 9, "bold"))
        self.priority_indicator.pack(side="left", padx=(12, 0))

        # Separator line
        sep = tk.Frame(self.root, bg="#e0e0e0", height=1)
        sep.grid(row=0, column=0, sticky="ew", pady=(69, 0))

        # Calendar body
        body = tk.Frame(self.root, bg="#ffffff")
        body.grid(row=1, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        # Canvas for week grid
        self.canvas = tk.Canvas(body, bg="#ffffff", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        
        vscroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        vscroll.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=vscroll.set)

        # Bind events
        self.canvas.bind("<Configure>", lambda e: self._render())
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)

        # Status bar with capacity warnings
        status_frame = tk.Frame(self.root, bg="#f8f9fa", height=32)
        status_frame.grid(row=2, column=0, sticky="ew")
        status_frame.grid_propagate(False)
        status_frame.columnconfigure(0, weight=1)
        
        self.status = tk.Label(status_frame, text="", bg="#f8f9fa", fg="#5f6368",
                              font=("Segoe UI", 9), anchor="w")
        self.status.grid(row=0, column=0, sticky="w", padx=16, pady=8)
        
        # Capacity warning indicator
        self.capacity_warning_label = tk.Label(status_frame, text="", bg="#f8f9fa", fg="#ea4335",
                                               font=("Segoe UI", 9, "bold"), anchor="e")
        self.capacity_warning_label.grid(row=0, column=1, sticky="e", padx=16, pady=8)

    def _create_nav_button(self, parent, text, command):
        btn = tk.Button(parent, text=text, command=command,
                       font=("Segoe UI", 11), bg="#ffffff", fg="#5f6368",
                       relief="flat", padx=12, pady=6, cursor="hand2",
                       borderwidth=1, highlightthickness=0)
        btn.bind("<Enter>", lambda e: btn.config(bg="#f1f3f4"))
        btn.bind("<Leave>", lambda e: btn.config(bg="#ffffff"))
        return btn

    def _create_action_button(self, parent, text, command):
        btn = tk.Button(parent, text=text, command=command,
                       font=("Segoe UI", 10), bg="#ffffff", fg="#5f6368",
                       relief="flat", padx=16, pady=8, cursor="hand2",
                       borderwidth=1, highlightthickness=0)
        btn.bind("<Enter>", lambda e: btn.config(bg="#f1f3f4"))
        btn.bind("<Leave>", lambda e: btn.config(bg="#ffffff"))
        return btn

    def _create_primary_button(self, parent, text, command):
        btn = tk.Button(parent, text=text, command=command,
                       font=("Segoe UI", 10, "bold"), bg="#1a73e8", fg="#ffffff",
                       relief="flat", padx=20, pady=8, cursor="hand2",
                       borderwidth=0, highlightthickness=0)
        btn.bind("<Enter>", lambda e: btn.config(bg="#1557b0"))
        btn.bind("<Leave>", lambda e: btn.config(bg="#1a73e8"))
        return btn

    def prev_week(self):
        self.week_start = self.week_start - timedelta(days=7)
        self.refresh_events()

    def next_week(self):
        self.week_start = self.week_start + timedelta(days=7)
        self.refresh_events()

    def go_today(self):
        self.today_date = datetime.now().date()
        self.week_start = self.today_date - timedelta(days=self.today_date.weekday())
        self.refresh_events()

    def _week_range(self):
        start_dt = datetime(self.week_start.year, self.week_start.month,
                           self.week_start.day, tzinfo=timezone.utc)
        end_dt = start_dt + timedelta(days=7)
        return start_dt.isoformat(), end_dt.isoformat()

    def refresh_events(self, skip_replan: bool = False):
        time_min, time_max = self._week_range()
        week_end = self.week_start + timedelta(days=6)
        title = f"{self.week_start.strftime('%b %d')} – {week_end.strftime('%b %d, %Y')}"
        self.date_label.config(text=title)
        try:
            items = self.client.list_events(time_min, time_max, max_results=250)
            self.events = items
            self._render()
            self.status.config(text=f"{len(items)} event(s) this week")
            
            # Update AI analysis
            self._update_ai_analysis()
            
            # Check if auto-replan is needed (skip if we just replanned)
            if self.optimizer.replan_on_event_change and not skip_replan and not self._is_replanning:
                self._check_and_replan()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load events: {e}")

    def _render(self):
        c = self.canvas
        c.delete("all")
        self.render_boxes = []
        
        width = max(c.winfo_width(), 1200)
        hour_h = 60
        total_hours = 24
        header_h = 60
        all_day_h = 40
        grid_top = header_h + all_day_h
        grid_h = hour_h * total_hours
        total_h = grid_top + grid_h
        
        left_gutter = 70
        days = [self.week_start + timedelta(days=i) for i in range(7)]
        col_w = (width - left_gutter - 20) / 7

        c.configure(scrollregion=(0, 0, width, total_h))

        # Draw header background
        c.create_rectangle(0, 0, width, header_h, fill="#ffffff", outline="")
        
        # Day headers
        for i, d in enumerate(days):
            x0 = left_gutter + i * col_w
            x_center = x0 + col_w / 2
            
            # Day name
            day_name = d.strftime("%a").upper()
            c.create_text(x_center, 20, text=day_name, fill="#70757a",
                         font=("Segoe UI", 10))
            
            # Day number with circle for today
            day_num = d.strftime("%d")
            is_today = (d == self.today_date)
            
            if is_today:
                # Blue circle for today
                c.create_oval(x_center - 18, 32, x_center + 18, 68,
                            fill="#1a73e8", outline="")
                c.create_text(x_center, 50, text=day_num, fill="#ffffff",
                            font=("Segoe UI", 16, "bold"))
            else:
                c.create_text(x_center, 50, text=day_num, fill="#3c4043",
                            font=("Segoe UI", 16))

        # All-day section
        c.create_rectangle(0, header_h, width, header_h + all_day_h,
                          fill="#fafafa", outline="")
        c.create_text(10, header_h + all_day_h / 2, anchor="w",
                     text="All day", fill="#70757a", font=("Segoe UI", 10))

        # Time grid
        for h in range(total_hours + 1):
            y = grid_top + h * hour_h
            # Hour line
            c.create_line(left_gutter, y, width, y, fill="#dadce0", width=1)
            # Hour label
            if h < total_hours:
                time_str = f"{h:02d}:00"
                c.create_text(left_gutter - 10, y + 10, anchor="e",
                            text=time_str, fill="#70757a", font=("Segoe UI", 10))

        # Vertical day separators
        for i in range(8):
            x = left_gutter + i * col_w
            c.create_line(x, grid_top, x, grid_top + grid_h, fill="#dadce0", width=1)

        # Highlight today column
        for i, d in enumerate(days):
            if d == self.today_date:
                x0 = left_gutter + i * col_w
                c.create_rectangle(x0 + 1, grid_top, x0 + col_w - 1, grid_top + grid_h,
                                 fill="#e8f0fe", outline="")
                break

        # Current time indicator (red line)
        now = datetime.now()
        if self.week_start <= now.date() <= self.week_start + timedelta(days=6):
            day_idx = (now.date() - self.week_start).days
            minutes_from_midnight = now.hour * 60 + now.minute
            y_now = grid_top + (minutes_from_midnight / 60.0) * hour_h
            x0 = left_gutter + day_idx * col_w
            x1 = x0 + col_w
            
            # Red circle
            c.create_oval(x0 + 4, y_now - 6, x0 + 16, y_now + 6, fill="#ea4335", outline="")
            # Red line
            c.create_line(x0 + 16, y_now, x1, y_now, fill="#ea4335", width=2)

        # Render events
        grouped = {i: [] for i in range(7)}
        all_day_events = {i: [] for i in range(7)}
        
        for ev in self.events:
            start_iso = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date")
            if not start_iso:
                continue
                
            if len(start_iso) <= 10:
                # All-day event
                try:
                    dt = datetime.fromisoformat(start_iso)
                    idx = (dt.date() - self.week_start).days
                    if 0 <= idx < 7:
                        all_day_events[idx].append(ev)
                except Exception:
                    continue
            else:
                # Timed event
                try:
                    dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).astimezone()
                    day_idx = (dt.date() - self.week_start).days
                    if 0 <= day_idx < 7:
                        grouped[day_idx].append(ev)
                except Exception:
                    continue

        # Draw all-day events
        for day_idx, items in all_day_events.items():
            for i, ev in enumerate(items):
                self._draw_all_day_event(day_idx, ev, i, left_gutter, col_w, header_h, all_day_h)

        # Draw timed events with lane layout
        for day_idx, items in grouped.items():
            if not items:
                continue
            layout = self._compute_lanes(items)
            for ev, lane, lane_count in layout:
                self._draw_timed_event(day_idx, ev, lane, lane_count,
                                      left_gutter, col_w, grid_top, hour_h)

    def _draw_all_day_event(self, day_idx, ev, offset, left_gutter, col_w, header_h, all_day_h):
        x0 = left_gutter + day_idx * col_w + 4
        x1 = left_gutter + (day_idx + 1) * col_w - 4
        y0 = header_h + 4 + offset * 20
        y1 = y0 + 18
        
        color = self._get_event_color(ev)
        rect = self.canvas.create_rectangle(x0, y0, x1, y1, fill=color,
                                           outline=color, width=0)
        title = ev.get("summary", "(no title)")
        self.canvas.create_text(x0 + 8, (y0 + y1) / 2, anchor="w",
                               text=title, fill="#ffffff",
                               font=("Segoe UI", 9, "bold"))
        self.render_boxes.append(((x0, y0, x1, y1), ev.get("id")))
        
        # Highlight top priority task if it matches this event
        if self.top_priority_task:
            event_title = ev.get("summary", "")
            if self.top_priority_task.get("title") in event_title or event_title in self.top_priority_task.get("title", ""):
                # Draw priority indicator
                self.canvas.create_oval(x1 - 20, y0 + 4, x1 - 4, y0 + 20,
                                      fill="#ea4335", outline="")
                self.canvas.create_text(x1 - 12, y0 + 12, text="1", fill="#ffffff",
                                      font=("Segoe UI", 10, "bold"))

    def _compute_lanes(self, events):
        def parse(iso):
            return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
        
        items = []
        for ev in events:
            s = ev.get("start", {}).get("dateTime")
            e = ev.get("end", {}).get("dateTime")
            if not s or not e:
                continue
            try:
                items.append((ev, parse(s), parse(e)))
            except Exception:
                continue
        
        items.sort(key=lambda x: x[1])
        lanes = []
        layout = []
        
        for ev, s, e in items:
            placed = False
            for i in range(len(lanes)):
                if s >= lanes[i]:
                    lanes[i] = e
                    layout.append((ev, i, None))
                    placed = True
                    break
            if not placed:
                lanes.append(e)
                layout.append((ev, len(lanes) - 1, None))
        
        lane_count = len(lanes)
        return [(ev, lane, lane_count) for (ev, lane, _) in layout]

    def _draw_timed_event(self, day_idx, ev, lane, lane_count,
                         left_gutter, col_w, grid_top, hour_h):
        start_iso = ev.get("start", {}).get("dateTime")
        end_iso = ev.get("end", {}).get("dateTime")
        if not start_iso or not end_iso:
            return
        
        s = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).astimezone()
        e = datetime.fromisoformat(end_iso.replace("Z", "+00:00")).astimezone()
        
        if e <= s:
            e = s + timedelta(minutes=30)
        
        minutes_s = s.hour * 60 + s.minute
        minutes_e = e.hour * 60 + e.minute
        
        x_col0 = left_gutter + day_idx * col_w
        lane_w = (col_w - 8) / max(1, lane_count)
        x0 = x_col0 + 4 + lane * lane_w
        x1 = x0 + lane_w - 4
        y0 = grid_top + (minutes_s / 60.0) * hour_h
        y1 = grid_top + (minutes_e / 60.0) * hour_h - 2
        
        # Minimum height for visibility
        if y1 - y0 < 24:
            y1 = y0 + 24
        
        color = self._get_event_color(ev)
        is_selected = (ev.get("id") == self.selected_event_id)
        
        # Draw event box with shadow
        if is_selected:
            # Shadow for selected
            self.canvas.create_rectangle(x0 + 2, y0 + 2, x1 + 2, y1 + 2,
                                        fill="#dadce0", outline="", width=0)
        
        rect = self.canvas.create_rectangle(x0, y0, x1, y1, fill=color,
                                           outline="#ffffff" if is_selected else color,
                                           width=3 if is_selected else 0)
        
        # Event title
        title = ev.get("summary", "(no title)")
        self.canvas.create_text(x0 + 8, y0 + 8, anchor="nw",
                               text=title, fill="#ffffff",
                               font=("Segoe UI", 10, "bold"), width=x1 - x0 - 16)
        
        # Time range
        time_str = f"{s.strftime('%H:%M')} – {e.strftime('%H:%M')}"
        self.canvas.create_text(x0 + 8, y0 + 26, anchor="nw",
                               text=time_str, fill="#ffffff",
                               font=("Segoe UI", 9))
        
        # Highlight top priority task if it matches this event
        if self.top_priority_task:
            event_title = ev.get("summary", "")
            task_title = self.top_priority_task.get("title", "")
            # Check if this event matches the top priority task
            if task_title and (task_title in event_title or event_title in task_title or 
                              any(tag in event_title for tag in ["[Task]", task_title[:20]])):
                # Draw priority indicator badge
                badge_size = 18
                self.canvas.create_oval(x1 - badge_size - 4, y0 + 4, x1 - 4, y0 + badge_size + 4,
                                      fill="#ea4335", outline="#ffffff", width=2)
                self.canvas.create_text(x1 - badge_size/2 - 4, y0 + badge_size/2 + 4, 
                                      text="1", fill="#ffffff",
                                      font=("Segoe UI", 9, "bold"))
        
        self.render_boxes.append(((x0, y0, x1, y1), ev.get("id")))

    def _get_event_color(self, ev):
        # Use event ID to consistently assign color
        ev_id = ev.get("id", "")
        idx = sum(ord(c) for c in ev_id) % len(EVENT_COLORS)
        return EVENT_COLORS[idx]

    def _on_click(self, event):
        x, y = event.x, event.y
        hit = None
        for bbox, ev_id in self.render_boxes:
            x0, y0, x1, y1 = bbox
            if x0 <= x <= x1 and y0 <= y <= y1:
                hit = ev_id
                break
        
        if hit:
            self.selected_event_id = hit
            self._render()
            self.status.config(text="Event selected (double-click to edit, Del to delete)")
        else:
            self.selected_event_id = None
            self._start_drag_create(x, y)

    def _on_double_click(self, event):
        if self.selected_event_id:
            self._edit_event(self.selected_event_id)

    def _start_drag_create(self, x, y):
        # Check if click is in grid area
        header_h = 60
        all_day_h = 40
        grid_top = header_h + all_day_h
        left_gutter = 70
        
        if y < grid_top or x < left_gutter:
            return
        
        width = max(self.canvas.winfo_width(), 1200)
        col_w = (width - left_gutter - 20) / 7
        day_idx = int((x - left_gutter) // col_w)
        
        if not (0 <= day_idx < 7):
            return
        
        self.drag_start = y
        self.drag_day_idx = day_idx

    def _on_drag(self, event):
        if self.drag_start is not None:
            # Visual feedback during drag (optional - could draw preview)
            pass

    def _on_release(self, event):
        if self.drag_start is not None and self.drag_day_idx is not None:
            y_end = event.y
            self._create_event_from_drag(self.drag_day_idx, self.drag_start, y_end)
        
        self.drag_start = None
        self.drag_day_idx = None

    def _create_event_from_drag(self, day_idx, y_start, y_end):
        header_h = 60
        all_day_h = 40
        grid_top = header_h + all_day_h
        hour_h = 60
        
        # Calculate times
        minutes_start = max(0, (y_start - grid_top) / hour_h * 60)
        minutes_end = max(0, (y_end - grid_top) / hour_h * 60)
        
        if minutes_end < minutes_start:
            minutes_start, minutes_end = minutes_end, minutes_start
        
        # Snap to 15-minute intervals
        minutes_start = int(minutes_start // 15) * 15
        minutes_end = int(minutes_end // 15) * 15
        
        # Minimum 30 minutes
        if minutes_end - minutes_start < 30:
            minutes_end = minutes_start + 30
        
        start_day = self.week_start + timedelta(days=day_idx)
        start_dt = datetime.combine(start_day,
                                    dt_time(hour=int(minutes_start // 60),
                                           minute=int(minutes_start % 60))).astimezone()
        end_dt = datetime.combine(start_day,
                                  dt_time(hour=int(minutes_end // 60),
                                         minute=int(minutes_end % 60))).astimezone()
        
        self._quick_add_event(start_dt, end_dt)

    def _quick_add_event(self, start_local, end_local):
        top = tk.Toplevel(self.root)
        top.title("Create Event")
        top.geometry("480x280")
        top.transient(self.root)
        top.grab_set()
        
        frm = tk.Frame(top, bg="#ffffff", padx=24, pady=20)
        frm.pack(fill="both", expand=True)
        
        tk.Label(frm, text="Event Title", bg="#ffffff", fg="#3c4043",
                font=("Segoe UI", 10)).grid(row=0, column=0, sticky="w", pady=(0, 4))
        title_var = tk.StringVar()
        title_entry = tk.Entry(frm, textvariable=title_var, font=("Segoe UI", 12),
                              relief="solid", borderwidth=1)
        title_entry.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        title_entry.focus()
        
        tk.Label(frm, text=f"📅 {start_local.strftime('%A, %B %d, %Y')}",
                bg="#ffffff", fg="#5f6368", font=("Segoe UI", 10)).grid(
                row=2, column=0, sticky="w", pady=(0, 8))
        
        tk.Label(frm, text=f"🕐 {start_local.strftime('%H:%M')} – {end_local.strftime('%H:%M')}",
                bg="#ffffff", fg="#5f6368", font=("Segoe UI", 10)).grid(
                row=3, column=0, sticky="w", pady=(0, 16))
        
        frm.columnconfigure(0, weight=1)
        
        btn_frame = tk.Frame(frm, bg="#ffffff")
        btn_frame.grid(row=4, column=0, sticky="e", pady=(8, 0))
        
        cancel_btn = tk.Button(btn_frame, text="Cancel", command=top.destroy,
                               font=("Segoe UI", 10), bg="#ffffff", fg="#5f6368",
                               relief="flat", padx=16, pady=8, cursor="hand2")
        cancel_btn.pack(side="right", padx=4)
        cancel_btn.bind("<Enter>", lambda e: cancel_btn.config(bg="#f1f3f4"))
        cancel_btn.bind("<Leave>", lambda e: cancel_btn.config(bg="#ffffff"))
        
        def on_save():
            title = title_var.get().strip() or "Untitled Event"
            try:
                self.client.add_event(
                    summary=title,
                    start=to_utc_iso(start_local),
                    end=to_utc_iso(end_local),
                    description=""
                )
                top.destroy()
                # Skip replan check - we'll check after refresh
                self.refresh_events(skip_replan=False)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to create event: {e}")
        
        save_btn = tk.Button(btn_frame, text="Save", command=on_save,
                            font=("Segoe UI", 10, "bold"), bg="#1a73e8", fg="#ffffff",
                            relief="flat", padx=20, pady=8, cursor="hand2")
        save_btn.pack(side="right", padx=4)
        save_btn.bind("<Enter>", lambda e: save_btn.config(bg="#1557b0"))
        save_btn.bind("<Leave>", lambda e: save_btn.config(bg="#1a73e8"))
        
        title_entry.bind("<Return>", lambda e: on_save())

    def _edit_event(self, event_id):
        ev = next((e for e in self.events if e.get("id") == event_id), None)
        if not ev:
            return
        
        # Simple edit dialog (could be enhanced)
        new_title = tk.simpledialog.askstring("Edit Event",
                                              "Enter new title:",
                                              initialvalue=ev.get("summary", ""))
        if new_title:
            try:
                ev["summary"] = new_title
                self.client.update_event(event_id, ev)
                # Skip replan check - we'll check after refresh
                self.refresh_events(skip_replan=False)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to update: {e}")

    def add_event_dialog(self):
        # Default to today at 9 AM
        start_local = datetime.combine(self.today_date, dt_time(hour=9, minute=0)).astimezone()
        end_local = start_local + timedelta(hours=1)
        self._quick_add_event(start_local, end_local)

    def delete_selected(self):
        if not self.selected_event_id:
            messagebox.showinfo("Delete Event", "Select an event first.")
            return
        
        ev = next((e for e in self.events if e.get("id") == self.selected_event_id), None)
        if not ev:
            return
        
        if messagebox.askyesno("Delete Event",
                              f"Delete '{ev.get('summary', '(no title)')}'?"):
            try:
                self.client.delete_event(self.selected_event_id)
                self.selected_event_id = None
                # Skip replan check - we'll check after refresh
                self.refresh_events(skip_replan=False)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to delete: {e}")

    def _update_user_label(self):
        """Update the user email label"""
        try:
            email = self.client.get_user_email()
            if email and email != "Not signed in":
                # Truncate long emails
                display = email if len(email) <= 30 else email[:27] + "..."
                self.user_label.config(text=display)
            else:
                self.user_label.config(text="Not signed in")
        except Exception:
            self.user_label.config(text="Not signed in")

    def _show_account_menu(self):
        """Show account menu with sign in/out options"""
        menu = tk.Menu(self.root, tearoff=0)
        
        email = self.client.get_user_email()
        if email and email != "Not signed in":
            menu.add_command(label=f"Signed in as: {email}", state="disabled")
            menu.add_separator()
            menu.add_command(label="Switch Account", command=self._switch_account)
            menu.add_command(label="Sign Out", command=self._sign_out)
        else:
            menu.add_command(label="Not signed in", state="disabled")
            menu.add_separator()
            menu.add_command(label="Sign In", command=self._sign_in)
        
        # Show menu at cursor position
        try:
            menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally:
            menu.grab_release()

    def _sign_in(self):
        """Sign in to Google account"""
        try:
            self.status.config(text="Opening browser for sign in...")
            self.client.sign_in()
            self._update_user_label()
            self.refresh_events()
            messagebox.showinfo("Success", f"Signed in as {self.client.get_user_email()}")
        except Exception as e:
            messagebox.showerror("Sign In Error", f"Failed to sign in: {e}")
            self.status.config(text="Sign in failed")

    def _sign_out(self):
        """Sign out from current account"""
        if messagebox.askyesno("Sign Out", "Are you sure you want to sign out?"):
            try:
                self.client.sign_out()
                self._update_user_label()
                self.events = []
                self._render()
                self.status.config(text="Signed out successfully")
                messagebox.showinfo("Success", "Signed out successfully")
            except Exception as e:
                messagebox.showerror("Sign Out Error", f"Failed to sign out: {e}")

    def _switch_account(self):
        """Switch to a different Google account"""
        if messagebox.askyesno("Switch Account", 
                              "This will sign you out and prompt you to sign in with a different account. Continue?"):
            try:
                self.status.config(text="Switching account...")
                self.client.sign_in()
                self._update_user_label()
                self.refresh_events()
                messagebox.showinfo("Success", f"Switched to {self.client.get_user_email()}")
            except Exception as e:
                messagebox.showerror("Switch Account Error", f"Failed to switch account: {e}")

    def _show_task_manager(self):
        """Show task management dialog"""
        dialog = tk.Toplevel(self.root)
        dialog.title("Smart Task Scheduler")
        dialog.geometry("800x600")
        dialog.transient(self.root)
        
        # Main container
        main = tk.Frame(dialog, bg="#ffffff")
        main.pack(fill="both", expand=True, padx=20, pady=20)
        
        # Title
        tk.Label(main, text="📋 Smart Task Scheduler", bg="#ffffff", fg="#202124",
                font=("Segoe UI", 18, "bold")).pack(anchor="w", pady=(0, 10))
        
        tk.Label(main, text="Add tasks and let AI schedule them based on deadlines, workload, and your availability",
                bg="#ffffff", fg="#5f6368", font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 20))
        
        # Task list frame
        list_frame = tk.Frame(main, bg="#ffffff")
        list_frame.pack(fill="both", expand=True, pady=(0, 15))
        
        # Task listbox
        task_listbox = tk.Listbox(list_frame, font=("Segoe UI", 10), height=15)
        task_listbox.pack(side="left", fill="both", expand=True)
        
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=task_listbox.yview)
        scrollbar.pack(side="right", fill="y")
        task_listbox.config(yscrollcommand=scrollbar.set)
        
        def refresh_task_list():
            task_listbox.delete(0, tk.END)
            for i, task in enumerate(self.scheduler.tasks):
                due_str = task.due_date.strftime("%m/%d %H:%M")
                priority_stars = "⭐" * task.priority
                status = "✓" if task.scheduled_start else "○"
                task_listbox.insert(tk.END, 
                    f"{status} {task.title} | {task.duration_hours}h | Due: {due_str} {priority_stars}")
        
        # Buttons frame
        btn_frame = tk.Frame(main, bg="#ffffff")
        btn_frame.pack(fill="x", pady=(0, 15))
        
        def add_task_dialog():
            add_win = tk.Toplevel(dialog)
            add_win.title("Add Task")
            add_win.geometry("500x450")
            add_win.transient(dialog)
            add_win.grab_set()
            
            frm = tk.Frame(add_win, bg="#ffffff", padx=24, pady=20)
            frm.pack(fill="both", expand=True)
            frm.columnconfigure(1, weight=1)
            
            row = 0
            tk.Label(frm, text="Task Title *", bg="#ffffff", fg="#3c4043",
                    font=("Segoe UI", 10)).grid(row=row, column=0, sticky="w", pady=4)
            title_var = tk.StringVar()
            tk.Entry(frm, textvariable=title_var, font=("Segoe UI", 11)).grid(
                row=row, column=1, sticky="ew", pady=4)
            
            row += 1
            tk.Label(frm, text="Duration (hours) *", bg="#ffffff", fg="#3c4043",
                    font=("Segoe UI", 10)).grid(row=row, column=0, sticky="w", pady=4)
            duration_var = tk.StringVar(value="2.0")
            tk.Entry(frm, textvariable=duration_var, font=("Segoe UI", 11)).grid(
                row=row, column=1, sticky="ew", pady=4)
            
            row += 1
            tk.Label(frm, text="Due Date (days from now) *", bg="#ffffff", fg="#3c4043",
                    font=("Segoe UI", 10)).grid(row=row, column=0, sticky="w", pady=4)
            due_days_var = tk.StringVar(value="3")
            tk.Entry(frm, textvariable=due_days_var, font=("Segoe UI", 11)).grid(
                row=row, column=1, sticky="ew", pady=4)
            
            row += 1
            tk.Label(frm, text="Priority (1-5) *", bg="#ffffff", fg="#3c4043",
                    font=("Segoe UI", 10)).grid(row=row, column=0, sticky="w", pady=4)
            priority_var = tk.StringVar(value="3")
            priority_spin = tk.Spinbox(frm, from_=1, to=5, textvariable=priority_var,
                                      font=("Segoe UI", 11), width=10)
            priority_spin.grid(row=row, column=1, sticky="w", pady=4)
            
            row += 1
            rest_var = tk.BooleanVar(value=False)
            tk.Checkbutton(frm, text="Need rest break after this task", variable=rest_var,
                          bg="#ffffff", font=("Segoe UI", 10)).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=4)
            
            row += 1
            split_var = tk.BooleanVar(value=True)
            tk.Checkbutton(frm, text="Split large tasks across multiple days", variable=split_var,
                          bg="#ffffff", font=("Segoe UI", 10)).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=4)
            
            row += 1
            tk.Label(frm, text="Max hours per session", bg="#ffffff", fg="#3c4043",
                    font=("Segoe UI", 10)).grid(row=row, column=0, sticky="w", pady=4)
            max_session_var = tk.StringVar(value="3.0")
            tk.Entry(frm, textvariable=max_session_var, font=("Segoe UI", 11), width=10).grid(
                row=row, column=1, sticky="w", pady=4)
            
            row += 1
            tk.Label(frm, text="Description", bg="#ffffff", fg="#3c4043",
                    font=("Segoe UI", 10)).grid(row=row, column=0, sticky="nw", pady=4)
            desc_text = tk.Text(frm, height=4, font=("Segoe UI", 10))
            desc_text.grid(row=row, column=1, sticky="ew", pady=4)
            
            row += 1
            btn_row = tk.Frame(frm, bg="#ffffff")
            btn_row.grid(row=row, column=0, columnspan=2, sticky="e", pady=(15, 0))
            
            def save_task():
                try:
                    title = title_var.get().strip()
                    duration = float(duration_var.get())
                    due_days = int(due_days_var.get())
                    priority = int(priority_var.get())
                    max_session = float(max_session_var.get())
                    
                    if not title:
                        messagebox.showerror("Error", "Please enter a task title")
                        return
                    
                    due_date = datetime.now().astimezone() + timedelta(days=due_days)
                    
                    task = Task(
                        title=title,
                        duration_hours=duration,
                        due_date=due_date,
                        priority=priority,
                        rest_after=rest_var.get(),
                        description=desc_text.get("1.0", "end").strip(),
                        allow_split=split_var.get(),
                        max_session_hours=max_session
                    )
                    
                    self.scheduler.add_task(task)
                    refresh_task_list()
                    add_win.destroy()
                    
                    # Show info about splitting if task is large
                    if duration > max_session and split_var.get():
                        num_sessions = int(duration / max_session) + (1 if duration % max_session > 0 else 0)
                        messagebox.showinfo("Task Added", 
                            f"Task '{title}' added!\n\n📊 This task will be split into {num_sessions} sessions of ~{duration/num_sessions:.1f}h each.")
                    else:
                        messagebox.showinfo("Success", f"Task '{title}' added!")
                    
                except ValueError as e:
                    messagebox.showerror("Invalid Input", "Please check your input values")
            
            tk.Button(btn_row, text="Cancel", command=add_win.destroy,
                     font=("Segoe UI", 10), bg="#ffffff", fg="#5f6368",
                     relief="flat", padx=16, pady=8).pack(side="right", padx=4)
            tk.Button(btn_row, text="Add Task", command=save_task,
                     font=("Segoe UI", 10, "bold"), bg="#1a73e8", fg="#ffffff",
                     relief="flat", padx=20, pady=8).pack(side="right", padx=4)
        
        def remove_task():
            selection = task_listbox.curselection()
            if not selection:
                messagebox.showinfo("Remove Task", "Please select a task to remove")
                return
            
            idx = selection[0]
            task = self.scheduler.tasks[idx]
            if messagebox.askyesno("Remove Task", f"Remove '{task.title}'?"):
                self.scheduler.tasks.pop(idx)
                refresh_task_list()
        
        def schedule_all():
            if not self.scheduler.tasks:
                messagebox.showinfo("No Tasks", "Please add some tasks first")
                return
            
            try:
                self.status.config(text="🤖 AI is optimizing your schedule...")
                
                # Use AI to schedule tasks optimally
                start_date = datetime.now().astimezone()
                tomorrow = start_date.date() + timedelta(days=1)
                start_date = datetime.combine(tomorrow, self.scheduler.schedule.work_start).astimezone()
                
                # Get existing events for context
                end_date = start_date + timedelta(days=14)
                existing_events = self.scheduler.get_existing_events(start_date, end_date)
                
                # Use AI scheduler
                scheduled = self.optimizer._ai_schedule_tasks(
                    self.scheduler.tasks, 
                    existing_events, 
                    start_date, 
                    days=14
                )
                
                if not scheduled:
                    messagebox.showwarning("Scheduling Failed", 
                                         "AI could not schedule tasks. Check your calendar availability.")
                    return
                
                # Show summary
                summary = self.scheduler.get_schedule_summary(scheduled)
                
                result_win = tk.Toplevel(dialog)
                result_win.title("Schedule Preview")
                result_win.geometry("700x500")
                result_win.transient(dialog)
                
                text_frame = tk.Frame(result_win, bg="#ffffff", padx=20, pady=20)
                text_frame.pack(fill="both", expand=True)
                
                text_widget = tk.Text(text_frame, font=("Consolas", 10), wrap="word")
                text_widget.pack(fill="both", expand=True)
                text_widget.insert("1.0", summary)
                text_widget.config(state="disabled")
                
                btn_frame = tk.Frame(result_win, bg="#ffffff", padx=20, pady=10)
                btn_frame.pack(fill="x")
                
                # Add breaks checkbox
                add_breaks_var = tk.BooleanVar(value=True)
                tk.Checkbutton(btn_frame, text="☕ Include break times in calendar",
                              variable=add_breaks_var, bg="#ffffff",
                              font=("Segoe UI", 10)).pack(side="left", padx=10)
                
                def add_to_calendar():
                    # Temporarily disable auto-replan to prevent optimization after adding
                    original_replan_setting = self.optimizer.replan_on_event_change
                    self.optimizer.replan_on_event_change = False
                    
                    try:
                    self.scheduler.add_tasks_to_calendar(scheduled, tag="[Task]", 
                                                        add_breaks=add_breaks_var.get())
                    
                    # Remove scheduled tasks from the task list
                    for task in scheduled:
                        if task in self.scheduler.tasks:
                            self.scheduler.tasks.remove(task)
                    
                    result_win.destroy()
                    dialog.destroy()
                        
                        # Refresh without triggering replan (tasks are already optimally scheduled)
                        self.refresh_events(skip_replan=True)
                    
                        msg = f"Added {len(scheduled)} tasks to your calendar!\n\n"
                        msg += f"✓ Tasks optimally scheduled from tomorrow\n"
                        msg += f"✓ Deadlines preserved\n"
                        msg += f"✓ Workload balanced"
                    if add_breaks_var.get():
                            msg += f"\n\n☕ Break times have been scheduled between tasks."
                    msg += f"\n\n✓ {len(scheduled)} tasks removed from scheduler"
                    messagebox.showinfo("Success", msg)
                    finally:
                        # Restore original replan setting
                        self.optimizer.replan_on_event_change = original_replan_setting
                
                tk.Button(btn_frame, text="Cancel", command=result_win.destroy,
                         font=("Segoe UI", 10), bg="#ffffff", fg="#5f6368",
                         relief="flat", padx=16, pady=8).pack(side="right", padx=4)
                tk.Button(btn_frame, text="Add to Calendar", command=add_to_calendar,
                         font=("Segoe UI", 10, "bold"), bg="#1a73e8", fg="#ffffff",
                         relief="flat", padx=20, pady=8).pack(side="right", padx=4)
                
            except Exception as e:
                messagebox.showerror("Scheduling Error", f"Failed to schedule tasks: {e}")
                self.status.config(text="Scheduling failed")
        
        def configure_schedule():
            config_win = tk.Toplevel(dialog)
            config_win.title("Work Schedule Settings")
            config_win.geometry("450x400")
            config_win.transient(dialog)
            config_win.grab_set()
            
            frm = tk.Frame(config_win, bg="#ffffff", padx=24, pady=20)
            frm.pack(fill="both", expand=True)
            frm.columnconfigure(1, weight=1)
            
            row = 0
            tk.Label(frm, text="Work Hours", bg="#ffffff", fg="#202124",
                    font=("Segoe UI", 12, "bold")).grid(row=row, column=0, columnspan=2,
                                                        sticky="w", pady=(0, 10))
            
            row += 1
            tk.Label(frm, text="Work Start Time", bg="#ffffff", fg="#3c4043",
                    font=("Segoe UI", 10)).grid(row=row, column=0, sticky="w", pady=4)
            work_start_var = tk.StringVar(value=self.scheduler.schedule.work_start.strftime("%H:%M"))
            tk.Entry(frm, textvariable=work_start_var, font=("Segoe UI", 11)).grid(
                row=row, column=1, sticky="ew", pady=4)
            
            row += 1
            tk.Label(frm, text="Work End Time", bg="#ffffff", fg="#3c4043",
                    font=("Segoe UI", 10)).grid(row=row, column=0, sticky="w", pady=4)
            work_end_var = tk.StringVar(value=self.scheduler.schedule.work_end.strftime("%H:%M"))
            tk.Entry(frm, textvariable=work_end_var, font=("Segoe UI", 11)).grid(
                row=row, column=1, sticky="ew", pady=4)
            
            row += 1
            tk.Label(frm, text="Max Work Hours Per Day *", bg="#ffffff", fg="#3c4043",
                    font=("Segoe UI", 10, "bold")).grid(row=row, column=0, sticky="w", pady=4)
            max_hours_var = tk.StringVar(value=str(self.scheduler.schedule.max_hours_per_day))
            max_hours_entry = tk.Entry(frm, textvariable=max_hours_var, font=("Segoe UI", 11))
            max_hours_entry.grid(row=row, column=1, sticky="ew", pady=4)
            
            row += 1
            tk.Label(frm, text="(Hard limit - no day will exceed this)",
                    bg="#ffffff", fg="#70757a", font=("Segoe UI", 9, "italic")).grid(
                row=row, column=1, sticky="w", pady=(0, 8))
            
            row += 1
            tk.Label(frm, text="", bg="#ffffff").grid(row=row, column=0, pady=10)
            
            row += 1
            tk.Label(frm, text="Sleep Schedule", bg="#ffffff", fg="#202124",
                    font=("Segoe UI", 12, "bold")).grid(row=row, column=0, columnspan=2,
                                                        sticky="w", pady=(0, 10))
            
            row += 1
            tk.Label(frm, text="Sleep Start Time", bg="#ffffff", fg="#3c4043",
                    font=("Segoe UI", 10)).grid(row=row, column=0, sticky="w", pady=4)
            sleep_start_var = tk.StringVar(value=self.scheduler.schedule.sleep_start.strftime("%H:%M"))
            tk.Entry(frm, textvariable=sleep_start_var, font=("Segoe UI", 11)).grid(
                row=row, column=1, sticky="ew", pady=4)
            
            row += 1
            tk.Label(frm, text="Sleep End Time", bg="#ffffff", fg="#3c4043",
                    font=("Segoe UI", 10)).grid(row=row, column=0, sticky="w", pady=4)
            sleep_end_var = tk.StringVar(value=self.scheduler.schedule.sleep_end.strftime("%H:%M"))
            tk.Entry(frm, textvariable=sleep_end_var, font=("Segoe UI", 11)).grid(
                row=row, column=1, sticky="ew", pady=4)
            
            row += 1
            tk.Label(frm, text="", bg="#ffffff").grid(row=row, column=0, pady=10)
            
            row += 1
            tk.Label(frm, text="Buffer & Spacing", bg="#ffffff", fg="#202124",
                    font=("Segoe UI", 12, "bold")).grid(row=row, column=0, columnspan=2,
                                                        sticky="w", pady=(0, 10))
            
            row += 1
            tk.Label(frm, text="Min buffer before deadline (hours)", bg="#ffffff", fg="#3c4043",
                    font=("Segoe UI", 10)).grid(row=row, column=0, sticky="w", pady=4)
            buffer_var = tk.StringVar(value=str(self.scheduler.balancer.min_buffer_hours if self.scheduler.balancer else 12))
            tk.Entry(frm, textvariable=buffer_var, font=("Segoe UI", 11)).grid(
                row=row, column=1, sticky="ew", pady=4)
            
            row += 1
            tk.Label(frm, text="Min buffer between tasks (min)", bg="#ffffff", fg="#3c4043",
                    font=("Segoe UI", 10)).grid(row=row, column=0, sticky="w", pady=4)
            task_buffer_var = tk.StringVar(value="15")
            tk.Entry(frm, textvariable=task_buffer_var, font=("Segoe UI", 11)).grid(
                row=row, column=1, sticky="ew", pady=4)
            
            row += 1
            btn_row = tk.Frame(frm, bg="#ffffff")
            btn_row.grid(row=row, column=0, columnspan=2, sticky="e", pady=(20, 0))
            
            def save_config():
                try:
                    # Parse and validate inputs
                    max_hours = float(max_hours_var.get())
                    
                    if max_hours <= 0 or max_hours > 16:
                        messagebox.showerror("Invalid Input", 
                                           "Max hours per day must be between 0 and 16")
                        return
                    
                    self.scheduler.schedule.work_start = datetime.strptime(
                        work_start_var.get(), "%H:%M").time()
                    self.scheduler.schedule.work_end = datetime.strptime(
                        work_end_var.get(), "%H:%M").time()
                    self.scheduler.schedule.sleep_start = datetime.strptime(
                        sleep_start_var.get(), "%H:%M").time()
                    self.scheduler.schedule.sleep_end = datetime.strptime(
                        sleep_end_var.get(), "%H:%M").time()
                    self.scheduler.schedule.max_hours_per_day = max_hours
                    
                    # Update balancer settings to respect the hard limit
                    if self.scheduler.balancer:
                        self.scheduler.balancer.min_buffer_hours = float(buffer_var.get())
                        self.scheduler.balancer.max_hours_per_day = max_hours
                        # Adjust ideal hours to be below max
                        self.scheduler.balancer.ideal_hours_per_day = min(
                            self.scheduler.balancer.ideal_hours_per_day,
                            max_hours * 0.75
                        )
                        self.scheduler.balancer.target_hours_per_day = min(
                            self.scheduler.balancer.target_hours_per_day,
                            max_hours * 0.58
                        )
                    
                    config_win.destroy()
                    messagebox.showinfo("Success", 
                                      f"Schedule settings updated!\n\n"
                                      f"✓ Max work: {max_hours}h per day (HARD LIMIT)\n"
                                      f"✓ Tasks complete {buffer_var.get()}h before deadlines\n"
                                      f"✓ {task_buffer_var.get()} min buffer between tasks")
                except ValueError:
                    messagebox.showerror("Invalid Input", "Please enter valid numbers")
                except Exception as e:
                    messagebox.showerror("Invalid Input", f"Please check your input: {e}")
            
            tk.Button(btn_row, text="Cancel", command=config_win.destroy,
                     font=("Segoe UI", 10), bg="#ffffff", fg="#5f6368",
                     relief="flat", padx=16, pady=8).pack(side="right", padx=4)
            tk.Button(btn_row, text="Save", command=save_config,
                     font=("Segoe UI", 10, "bold"), bg="#1a73e8", fg="#ffffff",
                     relief="flat", padx=20, pady=8).pack(side="right", padx=4)
        
        def show_workload_report():
            if not self.scheduler.balancer:
                messagebox.showinfo("Info", "Workload balancer not available")
                return
            
            report = self.scheduler.balancer.get_workload_report(self.scheduler.tasks, days=14)
            
            report_win = tk.Toplevel(dialog)
            report_win.title("Workload Balance Report")
            report_win.geometry("700x500")
            report_win.transient(dialog)
            
            text_frame = tk.Frame(report_win, bg="#ffffff", padx=20, pady=20)
            text_frame.pack(fill="both", expand=True)
            
            text_widget = tk.Text(text_frame, font=("Consolas", 10), wrap="word")
            text_widget.pack(fill="both", expand=True)
            text_widget.insert("1.0", report)
            text_widget.config(state="disabled")
            
            btn_frame = tk.Frame(report_win, bg="#ffffff", padx=20, pady=10)
            btn_frame.pack(fill="x")
            
            tk.Button(btn_frame, text="Close", command=report_win.destroy,
                     font=("Segoe UI", 10), bg="#ffffff", fg="#5f6368",
                     relief="flat", padx=16, pady=8).pack(side="right", padx=4)
        
        self._create_action_button(btn_frame, "+ Add Task", add_task_dialog).pack(side="left", padx=4)
        self._create_action_button(btn_frame, "✕ Remove", remove_task).pack(side="left", padx=4)
        self._create_action_button(btn_frame, "📊 Report", show_workload_report).pack(side="left", padx=4)
        self._create_action_button(btn_frame, "⚙️ Settings", configure_schedule).pack(side="left", padx=4)
        self._create_primary_button(btn_frame, "🤖 Schedule All", schedule_all).pack(side="right", padx=4)
        
        # Info text
        info_text = ("💡 Smart Workload Balancing:\n"
                    "   • Prevents overload with intelligent spacing\n"
                    "   • Distributes workload evenly across the week\n"
                    "   • Splits large tasks into focused sessions\n"
                    "   • Balances cognitive load (complex vs simple tasks)\n"
                    "   • Schedules mandatory rest days\n"
                    "   • Progressive loading (lighter Mon/Fri, peak Wed)\n"
                    "   • Click '📊 Report' to see workload analysis")
        tk.Label(main, text=info_text, bg="#f8f9fa", fg="#5f6368",
                font=("Segoe UI", 9), justify="left", anchor="w",
                padx=12, pady=12).pack(fill="x")
        
        refresh_task_list()
    
    def _update_ai_analysis(self):
        """Update AI analysis (priorities, capacity, bottlenecks)"""
        try:
            # Update priority analysis
            priority_analysis = self.optimizer.analyze_priorities()
            self.top_priority_task = priority_analysis.top_priority_task
            
            # Update priority indicator
            if self.top_priority_task:
                task_title = self.top_priority_task.get("title", "")
                if len(task_title) > 25:
                    task_title = task_title[:22] + "..."
                self.priority_indicator.config(
                    text=f"🎯 #1: {task_title}",
                    fg="#ea4335"
                )
            else:
                self.priority_indicator.config(text="")
            
            # Update capacity warnings
            self.capacity_warnings = self.optimizer.analyze_capacity(days=7)
            critical_warnings = [w for w in self.capacity_warnings if w.severity == "critical"]
            warning_level = [w for w in self.capacity_warnings if w.severity == "warning"]
            
            if critical_warnings:
                self.capacity_warning_label.config(
                    text=f"🔴 {len(critical_warnings)} critical overload(s)",
                    fg="#ea4335"
                )
            elif warning_level:
                self.capacity_warning_label.config(
                    text=f"⚠️  {len(warning_level)} capacity warning(s)",
                    fg="#f6bf26"
                )
            else:
                self.capacity_warning_label.config(text="")
            
            # Update bottlenecks
            self.bottlenecks = self.optimizer.detect_bottlenecks(days=7)
        except Exception as e:
            print(f"⚠️  Error updating AI analysis: {e}")
    
    def _check_and_replan(self):
        """Check if replanning is needed and trigger it (with cooldown to prevent loops)"""
        try:
            # Prevent recursive replanning
            if self._is_replanning:
                return
            
            # Cooldown check - don't replan too frequently
            now = datetime.now()
            if self._last_replan_time:
                time_since_last = (now - self._last_replan_time).total_seconds()
                if time_since_last < self._replan_cooldown:
                    return  # Too soon since last replan
            
            # Check if there are [Task] events in calendar that need shuffling
            events = self.events
            has_task_events = any(ev.get("summary", "").startswith("[Task]") for ev in events)
            
            # Check for scheduled tasks in scheduler
            has_scheduled_tasks = any(t.scheduled_start for t in self.scheduler.tasks)
            
            # Only replan if there are tasks to replan
            if not has_task_events and not has_scheduled_tasks:
                return
            
            # Check for critical capacity issues
            warnings = self.optimizer.analyze_capacity(days=3)
            critical = [w for w in warnings if w.severity == "critical"]
            
            # Auto-replan only if:
            # 1. Critical capacity issues detected (always replan)
            # 2. Task events exist AND we haven't just replanned (prevent loop)
            #    BUT only if tasks were added via event changes, not via task manager
            #    (tasks added via task manager are already optimally scheduled)
            should_replan = critical or (has_task_events and not self._is_replanning)
            
            if should_replan:
                # Set flag to prevent recursive calls
                self._is_replanning = True
                self._last_replan_time = now
                
                # Auto-replan in background
                def replan_async():
                    try:
                        reason = "Critical capacity overload detected" if critical else "Schedule change - shuffling tasks"
                        result = self.optimizer.auto_replan(reason)
                        
                        # Clear flag after replan completes
                        self.root.after(0, lambda: setattr(self, '_is_replanning', False))
                        
                        if result.get("status") == "success":
                            msg = f"🤖 AI Auto-Optimization Complete!\n\n"
                            if result.get('calendar_tasks'):
                                msg += f"📅 {result.get('calendar_tasks')} calendar tasks AI-optimized\n"
                            if result.get('scheduler_tasks'):
                                msg += f"📋 {result.get('scheduler_tasks')} scheduler tasks AI-optimized\n"
                            msg += f"\n✓ Deadlines preserved\n✓ Workload balanced\n✓ AI-optimized placement"
                            
                            self.root.after(0, lambda: messagebox.showinfo("Auto-Replan Complete", msg))
                            # Skip replan check when refreshing after our own replan
                            self.root.after(0, lambda: self.refresh_events(skip_replan=True))
                        elif result.get("status") == "no_tasks":
                            # No tasks to replan, that's fine
                            pass
                        else:
                            self.root.after(0, lambda: print(f"⚠️  Replanning: {result.get('message')}"))
                    except Exception as e:
                        self.root.after(0, lambda: setattr(self, '_is_replanning', False))
                        print(f"⚠️  Error in replan async: {e}")
                
                threading.Thread(target=replan_async, daemon=True).start()
        except Exception as e:
            self._is_replanning = False
            print(f"⚠️  Error in auto-replan check: {e}")
    
    def _shuffle_tasks(self):
        """Manually shuffle/reschedule all [Task] events"""
        try:
            self.status.config(text="🔀 Shuffling tasks...")
            
            def shuffle_async():
                result = self.optimizer.shuffle_tasks("Manual shuffle requested")
                self.root.after(0, lambda: self._handle_shuffle_result(result))
            
            threading.Thread(target=shuffle_async, daemon=True).start()
        except Exception as e:
            messagebox.showerror("Shuffle Error", f"Failed to shuffle tasks: {e}")
            self.status.config(text="Shuffle failed")
    
    def _handle_shuffle_result(self, result: Dict):
        """Handle the result of task shuffling"""
        if result.get("status") == "success":
            tasks_count = result.get("tasks_rescheduled", 0)
            tasks_kept = result.get("tasks_kept", 0)
            message = f"✅ AI-Optimized Schedule Complete!\n\n"
            message += f"🤖 {tasks_count} task(s) rescheduled by AI\n"
            if tasks_kept > 0:
                message += f"⚠️  {tasks_kept} task(s) kept at original times\n"
                message += f"   (AI couldn't find better slots)\n"
            message += f"\n✓ Deadlines preserved\n"
            message += f"✓ Workload balanced\n"
            message += f"✓ No overworking\n"
            message += f"✓ AI-optimized placement"
            
            messagebox.showinfo("Task Shuffle Complete", message)
            status_text = f"✅ {tasks_count} shuffled"
            if tasks_kept > 0:
                status_text += f", {tasks_kept} kept"
            self.status.config(text=status_text)
            # Skip replan check after manual shuffle (we just shuffled!)
            self.refresh_events(skip_replan=True)
        elif result.get("status") == "no_tasks":
            messagebox.showinfo("No Tasks", "No [Task] events found in calendar to shuffle.")
            self.status.config(text="No tasks to shuffle")
        else:
            messagebox.showerror("Shuffle Error", f"Failed to shuffle: {result.get('message', 'Unknown error')}")
            self.status.config(text="Shuffle failed")
    
    def _optimize_schedule(self):
        """Manually trigger schedule optimization"""
        try:
            self.status.config(text="🤖 Optimizing schedule...")
            result = self.optimizer.optimize_continuously()
            
            # Show results
            message = f"Optimization Complete!\n\n"
            message += f"• Warnings: {result.get('warnings', 0)}\n"
            message += f"• Bottlenecks: {result.get('bottlenecks', 0)}\n"
            message += f"• Optimizations: {len(result.get('optimizations', []))}"
            
            if result.get('optimizations'):
                message += "\n\nOptimizations applied:"
                for opt in result.get('optimizations', []):
                    message += f"\n• {opt.get('message', '')}"
            
            messagebox.showinfo("Schedule Optimization", message)
            self.refresh_events()
        except Exception as e:
            messagebox.showerror("Optimization Error", f"Failed to optimize: {e}")
            self.status.config(text="Optimization failed")
    
    def _show_health_report(self):
        """Show comprehensive schedule health report"""
        try:
            report = self.optimizer.get_schedule_health_report()
            
            report_win = tk.Toplevel(self.root)
            report_win.title("Schedule Health Report")
            report_win.geometry("700x600")
            report_win.transient(self.root)
            
            text_frame = tk.Frame(report_win, bg="#ffffff", padx=20, pady=20)
            text_frame.pack(fill="both", expand=True)
            
            text_widget = tk.Text(text_frame, font=("Consolas", 10), wrap="word", bg="#ffffff")
            text_widget.pack(fill="both", expand=True)
            text_widget.insert("1.0", report)
            text_widget.config(state="disabled")
            
            btn_frame = tk.Frame(report_win, bg="#ffffff", padx=20, pady=10)
            btn_frame.pack(fill="x")
            
            def optimize_from_report():
                report_win.destroy()
                self._optimize_schedule()
            
            tk.Button(btn_frame, text="Optimize Now", command=optimize_from_report,
                     font=("Segoe UI", 10, "bold"), bg="#1a73e8", fg="#ffffff",
                     relief="flat", padx=20, pady=8).pack(side="right", padx=4)
            tk.Button(btn_frame, text="Close", command=report_win.destroy,
                     font=("Segoe UI", 10), bg="#ffffff", fg="#5f6368",
                     relief="flat", padx=16, pady=8).pack(side="right", padx=4)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to generate health report: {e}")
    
    def _start_continuous_optimization(self):
        """Start background thread for continuous optimization"""
        if self.auto_optimize_running:
            return
        
        self.auto_optimize_running = True
        
        def optimize_loop():
            import time
            while self.auto_optimize_running:
                try:
                    # Run optimization every 5 minutes
                    time.sleep(300)
                    
                    if self.auto_optimize_running:
                        result = self.optimizer.optimize_continuously()
                        
                        # Update UI if critical issues found
                        if result.get('warnings', 0) > 0 or result.get('bottlenecks', 0) > 0:
                            self.root.after(0, self._update_ai_analysis)
                except Exception as e:
                    print(f"⚠️  Continuous optimization error: {e}")
        
        thread = threading.Thread(target=optimize_loop, daemon=True)
        thread.start()
    
    def _load_ai_client_async(self, use_hf: bool, hf_model: str, ai_api_key: str):
        """Load AI client in background thread to avoid blocking UI"""
        def load_client():
            ai_client = None
            
            if use_hf:
                # Show loading dialog for Hugging Face
                model_display = hf_model.split("/")[-1] if "/" in hf_model else hf_model
                self.root.after(0, self._show_loading_dialog, 
                              f"Loading AI model: {model_display}...\n\n"
                              f"Small models load in ~30 seconds.\n"
                              f"Large models may take a few minutes on first run.")
                
                try:
                    from hf_ai_client import HuggingFaceClientWrapper
                    # Enable auto-fallback to smaller models if main model fails
                    ai_client = HuggingFaceClientWrapper(model_name=hf_model, auto_fallback=True)
                    if not ai_client.ready:
                        ai_client = None
                        self.root.after(0, lambda: self.status.config(
                            text="⚠️  AI model not available - using rule-based logic"
                        ))
                        self.root.after(0, lambda: print("⚠️  Hugging Face model not available, falling back to rule-based logic"))
                    else:
                        # Update status with actual loaded model
                        loaded_model = ai_client.model_name.split("/")[-1] if "/" in ai_client.model_name else ai_client.model_name
                        self.root.after(0, lambda: self.status.config(
                            text=f"✅ AI model ready: {loaded_model}"
                        ))
                except Exception as e:
                    self.root.after(0, lambda: print(f"⚠️  Could not initialize Hugging Face: {e}"))
                    self.root.after(0, lambda: self.status.config(
                        text="⚠️  AI initialization failed - using rule-based logic"
                    ))
                    ai_client = None
                
                # Close loading dialog
                self.root.after(0, self._close_loading_dialog)
            else:
                # Gemini loads quickly, no dialog needed
                try:
                    from apollo.ai_client import GeminiClientWrapper
                    ai_client = GeminiClientWrapper(ai_api_key, "gemini-2.0-flash-exp") if ai_api_key else None
                except:
                    ai_client = None
            
            # Update optimizer with loaded client
            if ai_client:
                self.optimizer.ai_client = ai_client
                if not use_hf:  # Gemini status already set above
                    self.root.after(0, lambda: self.status.config(
                        text="✅ AI model ready: Gemini"
                    ))
                # Refresh analysis now that AI is available
                self.root.after(0, self._update_ai_analysis)
        
        # Start loading in background thread
        thread = threading.Thread(target=load_client, daemon=True)
        thread.start()
    
    def _show_loading_dialog(self, message: str):
        """Show loading dialog for model initialization"""
        if self._loading_dialog:
            return
        
        self._loading_dialog = tk.Toplevel(self.root)
        self._loading_dialog.title("Loading AI Model")
        self._loading_dialog.geometry("500x200")
        self._loading_dialog.transient(self.root)
        # Don't grab focus - allow user to use calendar while loading
        # self._loading_dialog.grab_set()
        
        # Center the dialog
        self._loading_dialog.update_idletasks()
        x = (self._loading_dialog.winfo_screenwidth() // 2) - (500 // 2)
        y = (self._loading_dialog.winfo_screenheight() // 2) - (200 // 2)
        self._loading_dialog.geometry(f"500x200+{x}+{y}")
        
        frame = tk.Frame(self._loading_dialog, bg="#ffffff", padx=30, pady=30)
        frame.pack(fill="both", expand=True)
        
        tk.Label(frame, text="🔄 Loading AI Model", bg="#ffffff", fg="#202124",
                font=("Segoe UI", 14, "bold")).pack(pady=(0, 10))
        
        tk.Label(frame, text=message, bg="#ffffff", fg="#5f6368",
                font=("Segoe UI", 10), justify="left", wraplength=440).pack(pady=(0, 20))
        
        # Progress bar
        self._loading_progress = ttk.Progressbar(frame, mode='indeterminate', length=400)
        self._loading_progress.pack(pady=(0, 10))
        self._loading_progress.start(10)
        
        tk.Label(frame, text="You can use the calendar while the model loads in the background",
                bg="#ffffff", fg="#70757a", font=("Segoe UI", 9, "italic")).pack()
    
    def _close_loading_dialog(self):
        """Close the loading dialog"""
        if self._loading_dialog:
            try:
                if hasattr(self, '_loading_progress'):
                    self._loading_progress.stop()
                self._loading_dialog.destroy()
                self._loading_dialog = None
            except:
                pass


if __name__ == "__main__":
    import os
    root = tk.Tk()
    
    # Try to get AI API key from environment or config
    ai_api_key = os.getenv("GOOGLE_AI_API_KEY") or os.getenv("GEMINI_API_KEY")
    
    app = CalendarApp(root, ai_api_key=ai_api_key)
    root.mainloop()
