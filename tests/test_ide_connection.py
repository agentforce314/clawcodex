"""Tests for IDE Integration subsystem."""
from __future__ import annotations

import unittest

from src.services.ide.connection import IDEConnectionManager
from src.services.ide.diagnostics import DiagnosticsCollector
from src.services.ide.selection import SelectionTracker
from src.services.ide.types import (
    IDEConnection,
    IDEDiagnostic,
    IDEDiagnosticSeverity,
    IDERange,
    IDESelection,
    IDEType,
)


class TestIDETypes(unittest.TestCase):
    def test_ide_types(self) -> None:
        self.assertEqual(IDEType.VSCODE.value, "vscode")
        self.assertEqual(IDEType.JETBRAINS.value, "jetbrains")

    def test_ide_range(self) -> None:
        r = IDERange(start_line=1, start_character=0, end_line=1, end_character=10)
        self.assertEqual(r.start_line, 1)

    def test_ide_selection(self) -> None:
        sel = IDESelection(
            file_path="/src/app.py",
            text="print('hello')",
            range=IDERange(1, 0, 1, 14),
            language_id="python",
        )
        self.assertEqual(sel.language_id, "python")

    def test_ide_diagnostic(self) -> None:
        diag = IDEDiagnostic(
            file_path="/src/app.py",
            message="Unused import",
            severity=IDEDiagnosticSeverity.WARNING,
        )
        self.assertEqual(diag.severity, IDEDiagnosticSeverity.WARNING)

    def test_ide_connection_capabilities(self) -> None:
        conn = IDEConnection(
            ide_type=IDEType.VSCODE,
            connected=True,
            capabilities={"selection": True, "diagnostics": True, "openFile": False},
        )
        self.assertTrue(conn.supports_selection)
        self.assertTrue(conn.supports_diagnostics)
        self.assertFalse(conn.supports_open_file)
        self.assertFalse(conn.supports_apply_edit)


class TestIDEConnectionManager(unittest.TestCase):
    def test_connect_disconnect(self) -> None:
        mgr = IDEConnectionManager()
        self.assertFalse(mgr.is_connected)

        conn = mgr.connect(IDEType.VSCODE, version="1.80.0")
        self.assertTrue(mgr.is_connected)
        self.assertEqual(conn.ide_type, IDEType.VSCODE)

        mgr.disconnect()
        self.assertFalse(mgr.is_connected)

    def test_connect_callback(self) -> None:
        mgr = IDEConnectionManager()
        connected_types: list[IDEType] = []
        mgr.on_connect_callback(lambda conn: connected_types.append(conn.ide_type))

        mgr.connect(IDEType.JETBRAINS)
        self.assertEqual(connected_types, [IDEType.JETBRAINS])

    def test_disconnect_callback(self) -> None:
        mgr = IDEConnectionManager()
        disconnected = []
        mgr.on_disconnect_callback(lambda: disconnected.append(True))

        mgr.connect(IDEType.VSCODE)
        mgr.disconnect()
        self.assertEqual(len(disconnected), 1)

    def test_register_handler(self) -> None:
        mgr = IDEConnectionManager()
        async def handler(params):
            return {"ok": True}
        mgr.register_handler("test/method", handler)
        self.assertIn("test/method", mgr._handlers)

    def test_notification_subscribe_unsubscribe(self) -> None:
        mgr = IDEConnectionManager()
        received = []
        unsub = mgr.on_notification("selection/changed", lambda p: received.append(p))

        mgr.handle_notification("selection/changed", {"file": "test.py"})
        self.assertEqual(len(received), 1)

        unsub()
        mgr.handle_notification("selection/changed", {"file": "test2.py"})
        self.assertEqual(len(received), 1)

    def test_handle_response_unknown_id(self) -> None:
        mgr = IDEConnectionManager()
        mgr.connect(IDEType.VSCODE)
        # handle_response for unknown id should not crash
        mgr.handle_response(999, result="ignored")


class TestSelectionTracker(unittest.TestCase):
    def test_update_and_current(self) -> None:
        tracker = SelectionTracker()
        self.assertIsNone(tracker.current)

        sel = IDESelection(
            file_path="test.py",
            text="hello",
            range=IDERange(1, 0, 1, 5),
        )
        tracker.update(sel)
        self.assertEqual(tracker.current.file_path, "test.py")

    def test_history(self) -> None:
        tracker = SelectionTracker(max_history=3)
        for i in range(5):
            tracker.update(IDESelection(
                file_path=f"file{i}.py",
                text=f"text{i}",
                range=IDERange(1, 0, 1, 5),
            ))
        # Max 3 in history
        self.assertEqual(len(tracker.history), 3)
        self.assertEqual(tracker.history[0].file_path, "file4.py")

    def test_listener(self) -> None:
        tracker = SelectionTracker()
        received = []
        unsub = tracker.on_selection(lambda s: received.append(s.file_path))

        tracker.update(IDESelection("f1.py", "t", IDERange(1, 0, 1, 1)))
        self.assertEqual(received, ["f1.py"])

        unsub()
        tracker.update(IDESelection("f2.py", "t", IDERange(1, 0, 1, 1)))
        self.assertEqual(received, ["f1.py"])

    def test_clear(self) -> None:
        tracker = SelectionTracker()
        tracker.update(IDESelection("f.py", "t", IDERange(1, 0, 1, 1)))
        tracker.clear()
        self.assertIsNone(tracker.current)


class TestDiagnosticsCollector(unittest.TestCase):
    def test_update_and_get(self) -> None:
        collector = DiagnosticsCollector()
        diags = [
            IDEDiagnostic("app.py", "Error 1", IDEDiagnosticSeverity.ERROR),
            IDEDiagnostic("app.py", "Warning 1", IDEDiagnosticSeverity.WARNING),
        ]
        collector.update_file("app.py", diags)
        self.assertEqual(len(collector.get_file("app.py")), 2)

    def test_get_errors(self) -> None:
        collector = DiagnosticsCollector()
        collector.update_file("a.py", [
            IDEDiagnostic("a.py", "err", IDEDiagnosticSeverity.ERROR),
            IDEDiagnostic("a.py", "warn", IDEDiagnosticSeverity.WARNING),
        ])
        collector.update_file("b.py", [
            IDEDiagnostic("b.py", "err2", IDEDiagnosticSeverity.ERROR),
        ])
        self.assertEqual(len(collector.get_errors()), 2)
        self.assertEqual(len(collector.get_errors("a.py")), 1)

    def test_clear(self) -> None:
        collector = DiagnosticsCollector()
        collector.update_file("a.py", [IDEDiagnostic("a.py", "err")])
        collector.update_file("b.py", [IDEDiagnostic("b.py", "err")])
        collector.clear("a.py")
        self.assertEqual(collector.file_count, 1)
        collector.clear()
        self.assertEqual(collector.file_count, 0)

    def test_listener(self) -> None:
        collector = DiagnosticsCollector()
        updates = []
        unsub = collector.on_update(lambda f, d: updates.append(f))
        collector.update_file("test.py", [])
        self.assertEqual(updates, ["test.py"])
        unsub()
        collector.update_file("test2.py", [])
        self.assertEqual(updates, ["test.py"])


if __name__ == "__main__":
    unittest.main()
