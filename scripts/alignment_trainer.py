import sys
import sqlite3
from datetime import datetime
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QListWidget, QTextEdit, QPushButton, 
                             QLabel, QLineEdit, QSplitter, QMessageBox, QComboBox)
from PyQt6.QtCore import Qt

DB_PATH = r"C:\KokertechAI\kokertech_vault.db"

class AlignmentTrainer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KokertechAI - RLHF Alignment Trainer")
        self.setGeometry(150, 150, 1000, 600)
        self.setStyleSheet("QWidget { font-family: 'Consolas', monospace; }")
        
        main_widget = QWidget()
        layout = QVBoxLayout(main_widget)
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # --- LEFT PANEL: INTERACTION HISTORY ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(QLabel("📜 Recent AI Interactions (Raw):"))
        
        self.interaction_list = QListWidget()
        self.interaction_list.itemSelectionChanged.connect(self.load_interaction)
        left_layout.addWidget(self.interaction_list)
        
        self.btn_refresh = QPushButton("🔄 Refresh Logs")
        self.btn_refresh.clicked.connect(self.load_history)
        left_layout.addWidget(self.btn_refresh)
        
        splitter.addWidget(left_panel)
        
        # --- RIGHT PANEL: AUDIT & OVERRIDE FORM ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        right_layout.addWidget(QLabel("🔍 Raw Interaction Audit Payload:"))
        self.audit_display = QTextEdit()
        self.audit_display.setReadOnly(True)
        self.audit_display.setStyleSheet("background-color: #F3F4F6; color: #1F2937; font-size: 11pt; padding: 10px;")
        right_layout.addWidget(self.audit_display)
        
        right_layout.addWidget(QLabel("⚠️ Inject Manual Behavioral Correction:"))
        
        form_layout = QHBoxLayout()
        self.correction_type = QComboBox()
        self.correction_type.addItems(["Logic/Format Error", "Tone Adjustment", "Hallucination", "Over-Apologetic"])
        form_layout.addWidget(self.correction_type)
        
        self.correction_input = QLineEdit()
        self.correction_input.setPlaceholderText("Describe exactly what the AI did wrong and how to fix it...")
        form_layout.addWidget(self.correction_input, stretch=1)
        right_layout.addLayout(form_layout)
        
        self.btn_submit = QPushButton("⚡ ENFORCE BEHAVIORAL OVERRIDE")
        self.btn_submit.setStyleSheet("background-color: #EF4444; color: white; font-weight: bold; padding: 10px;")
        self.btn_submit.clicked.connect(self.submit_correction)
        right_layout.addWidget(self.btn_submit)
        
        splitter.addWidget(right_panel)
        splitter.setSizes([350, 650])
        
        layout.addWidget(splitter)
        self.setCentralWidget(main_widget)
        
        self.load_history()

    def load_history(self):
        self.interaction_list.clear()
        try:
            conn = sqlite3.connect(DB_PATH, timeout=15.0)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT id, content FROM core_memories WHERE node_type = 'interaction' ORDER BY id DESC LIMIT 50")
                interactions = cursor.fetchall()
                
                for mem_id, content in interactions:
                    # Strip newlines for a clean preview in the list
                    preview = content.replace('\n', ' ')[:45] + "..."
                    self.interaction_list.addItem(f"ID {mem_id}: {preview}")
                    # Hide the full raw content inside the list item's data payload
                    self.interaction_list.item(self.interaction_list.count() - 1).setData(Qt.ItemDataRole.UserRole, content)
            finally:
                conn.close()
        except Exception as e:
            QMessageBox.critical(self, "DB Error", str(e))

    def load_interaction(self):
        selected = self.interaction_list.currentItem()
        if selected:
            content = selected.data(Qt.ItemDataRole.UserRole)
            self.audit_display.setPlainText(content)

    def submit_correction(self):
        correction = self.correction_input.text().strip()
        if not correction: return
        
        ctype = self.correction_type.currentText()
        ts = datetime.now().strftime('%H:%M:%S')
        
        try:
            conn = sqlite3.connect(DB_PATH, timeout=15.0)
            try:
                cursor = conn.cursor()
                # Manual overrides are injected with 100% confidence to bypass the Shadow Auditor limits
                # agent_id is NOT NULL in the canonical schema (memory_vault)
                cursor.execute(
                    "INSERT INTO bias_ledger (timestamp, agent_id, bias_type, confidence_score, description) VALUES (?, ?, ?, ?, ?)",
                    (ts, "alignment_trainer", f"MANUAL_OVERRIDE: {ctype}", 100, correction)
                )
                conn.commit()
            finally:
                conn.close()
            
            QMessageBox.information(self, "Directive Enforced", "Cognitive alignment directive saved. The Executive Core will adapt to this rule on the next inference cycle.")
            self.correction_input.clear()
        except Exception as e:
            QMessageBox.critical(self, "Write Error", str(e))

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AlignmentTrainer()
    window.show()
    sys.exit(app.exec())