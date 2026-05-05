from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from neon_ai.services.document_token_catalog import (
    TOKEN_SYNTAX_NOTE,
    grouped_document_tokens,
)
from neon_ai.ui.pages.document_studio_page import DocumentStudioPage
from neon_ai.ui.pages.generated_documents_page import GeneratedDocumentsPage
from neon_ai.ui.pages.path_settings_page import PathSettingsPage


class TemplateInstructionsPage(QWidget):
    def __init__(self, main_window=None) -> None:
        parent = main_window if isinstance(main_window, QWidget) else None
        super().__init__(parent)
        self._category_items: list[tuple[str, QTreeWidgetItem]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel("Template Instructions")
        heading.setStyleSheet("font-size: 22px; font-weight: 700;")
        layout.addWidget(heading)

        intro = QLabel(
            "Tokens are placeholders used in templates. When a document is generated, "
            "Argon replaces each token with real project, customer, vendor, estimate, RFQ, "
            "purchase order, or invoice data."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        example = QLabel(
            "{VendorName} becomes TEST_3MO_Eecol\n"
            "{RFQNumber} becomes 10\n"
            "{RFQRequestedMaterialTable} becomes a material table"
        )
        example.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        example.setStyleSheet(
            "background: #f5f5f5; border: 1px solid #d8d8d8; border-radius: 6px; "
            "padding: 10px; font-family: Consolas, 'Courier New', monospace;"
        )
        layout.addWidget(example)

        syntax_note = QLabel(TOKEN_SYNTAX_NOTE)
        syntax_note.setWordWrap(True)
        syntax_note.setStyleSheet("color: #555;")
        layout.addWidget(syntax_note)

        rfq_note = QLabel(
            "For RFQ material placement, include {RFQRequestedMaterialTable} in the RFQ "
            "Delivery Body template where the requested material table should appear. "
            "Legacy RFQ templates that already use {{RFQRequestedMaterialTable}} still render."
        )
        rfq_note.setWordWrap(True)
        rfq_note.setStyleSheet("color: #555;")
        layout.addWidget(rfq_note)

        search_label = QLabel("Search tokens...")
        search_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(search_label)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search tokens...")
        self.search_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search_edit)

        self.token_tree = QTreeWidget()
        self.token_tree.setColumnCount(4)
        self.token_tree.setHeaderLabels(["Token", "Description", "Example Output", "Used In"])
        self.token_tree.setAlternatingRowColors(True)
        self.token_tree.setRootIsDecorated(True)
        self.token_tree.setUniformRowHeights(True)
        self.token_tree.setSortingEnabled(False)
        self.token_tree.header().setStretchLastSection(True)
        self.token_tree.header().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.token_tree, 1)

        self._populate_token_tree()

    def refresh(self) -> None:
        self._apply_filter(self.search_edit.text())

    def _populate_token_tree(self) -> None:
        self.token_tree.clear()
        self._category_items.clear()

        group_font = QFont()
        group_font.setBold(True)

        for category, token_records in grouped_document_tokens():
            category_item = QTreeWidgetItem([category, "", "", ""])
            for column in range(4):
                category_item.setFont(column, group_font)
            category_item.setData(0, Qt.ItemDataRole.UserRole, category.lower())
            self.token_tree.addTopLevelItem(category_item)
            self._category_items.append((category, category_item))

            for entry in token_records:
                child = QTreeWidgetItem(
                    [
                        entry.token,
                        entry.description,
                        entry.example_output,
                        entry.used_in,
                    ]
                )
                search_blob = " ".join(
                    [
                        entry.category,
                        entry.token,
                        entry.description,
                        entry.example_output,
                        entry.used_in,
                    ]
                ).lower()
                child.setData(0, Qt.ItemDataRole.UserRole, search_blob)
                category_item.addChild(child)
            category_item.setExpanded(True)

        for column in range(4):
            self.token_tree.resizeColumnToContents(column)

    def _apply_filter(self, raw_query: str) -> None:
        query = str(raw_query or "").strip().lower()
        has_query = bool(query)

        for category_name, category_item in self._category_items:
            category_match = query in category_name.lower() if has_query else True
            visible_child_count = 0

            for child_index in range(category_item.childCount()):
                child = category_item.child(child_index)
                search_blob = str(child.data(0, Qt.ItemDataRole.UserRole) or "")
                child_match = not has_query or category_match or query in search_blob
                child.setHidden(not child_match)
                if child_match:
                    visible_child_count += 1

            category_visible = not has_query or category_match or visible_child_count > 0
            category_item.setHidden(not category_visible)
            category_item.setExpanded(category_visible and (has_query or visible_child_count > 0))


class DocumentControlPage(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__(main_window)
        self.main_window = main_window
        self.container = getattr(main_window, "container", None)
        self._generated_history_dialog: QDialog | None = None
        self._generated_history_page: GeneratedDocumentsPage | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        heading = QLabel("Template & Document Control")
        heading.setStyleSheet("font-size: 24px; font-weight: 700;")
        layout.addWidget(heading)

        subtitle = QLabel(
            "Manage templates, token instructions, and file path rules in one place. "
            "Generated history remains available as a secondary debug/audit view."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #555;")
        layout.addWidget(subtitle)

        utility_row = QHBoxLayout()
        utility_row.addStretch(1)
        self.open_generated_history_button = QPushButton("Open Generated History (Debug)")
        self.open_generated_history_button.setToolTip(
            "Generated history remains available for audit/debug use, but is no longer a primary workspace tab."
        )
        self.open_generated_history_button.clicked.connect(self.open_generated_history_dialog)
        utility_row.addWidget(self.open_generated_history_button)
        layout.addLayout(utility_row)

        self.tabs = QTabWidget()
        self.templates_page = DocumentStudioPage(main_window)
        self.instructions_page = TemplateInstructionsPage(main_window)
        self.path_settings_page = PathSettingsPage(main_window)
        self.tabs.addTab(self.templates_page, "Templates")
        self.tabs.addTab(self.instructions_page, "Instructions")
        self.tabs.addTab(self.path_settings_page, "File Paths")
        self.tabs.setCurrentWidget(self.templates_page)
        layout.addWidget(self.tabs, 1)

    def refresh(self) -> None:
        self.container = getattr(self.main_window, "container", None)
        for page in (self.templates_page, self.instructions_page, self.path_settings_page):
            refresh = getattr(page, "refresh", None)
            if callable(refresh):
                refresh()
        if self._generated_history_page is not None:
            refresh = getattr(self._generated_history_page, "refresh", None)
            if callable(refresh):
                refresh()

    def refresh_data(self) -> None:
        self.refresh()

    def open_generated_history_dialog(self) -> None:
        if self._generated_history_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Generated History")
            dialog.resize(1200, 800)
            dialog_layout = QVBoxLayout(dialog)
            helper = QLabel(
                "Generated document history remains available for audit/debug review. "
                "Primary day-to-day document control now focuses on Templates, Instructions, and File Paths."
            )
            helper.setWordWrap(True)
            helper.setStyleSheet("color: #555;")
            dialog_layout.addWidget(helper)

            self._generated_history_page = GeneratedDocumentsPage(self.main_window)
            dialog_layout.addWidget(self._generated_history_page, 1)

            close_button = QPushButton("Close")
            close_button.clicked.connect(dialog.close)
            footer = QHBoxLayout()
            footer.addStretch(1)
            footer.addWidget(close_button)
            dialog_layout.addLayout(footer)

            self._generated_history_dialog = dialog

        if self._generated_history_page is not None:
            refresh = getattr(self._generated_history_page, "refresh", None)
            if callable(refresh):
                refresh()

        self._generated_history_dialog.show()
        self._generated_history_dialog.raise_()
        self._generated_history_dialog.activateWindow()
