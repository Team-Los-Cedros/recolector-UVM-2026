"""Regresiones del mando de pista. No abren USB ni SSH."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pista_uvm import Session, measured_geometry


def reading(side,color,conf=4):
    return f"TCS {side} C=471 R=181 G=173 B=111 -> {color} (conf {conf})"


class Port:
    def __init__(self, moving):
        self.commands = []
        self.responses = []
        self.moving = moving

    def write(self, data):
        cmd = data.decode().strip()
        self.commands.append(cmd)
        if cmd == "t":
            self.responses = [reading("IZQ","azul"),reading("DER","azul")]
        elif cmd.startswith("m "):
            self.responses = list(self.moving)
        elif cmd == "estado":
            self.responses = ["# Garra: SLOT3 canal B"]
        else:
            self.responses = []

    def readline(self):
        return ((self.responses.pop(0)+"\n").encode() if self.responses else b"")


class PracticeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.s = Session(Path(folder.name))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def test_no_motion_without_margin_after_restarting_or_changing_claw(self):
        with self.assertRaises(ValueError):
            self.s.command("w 150")
        self.s.command("margen 80 25 12 2")
        self.s.command("w 200")
        self.assertEqual(self.s.free,73)
        self.s.command("cerrar 150")
        with self.assertRaises(ValueError):
            self.s.command("s 100")
        events = [json.loads(l) for l in self.s.journal.read_text(encoding="utf8").splitlines()]
        self.assertEqual([r["dato"] for r in events if r["evento"]=="TX" and r["dato"].startswith("m ")],["m 80 80 200"])

    def test_invalid_or_insufficient_budget(self):
        for cmd in ("margen nan 20 10 1","margen 10 20 10 1","margen 50 0 10 1","w 999"):
            with self.assertRaises(ValueError):
                self.s.command(cmd)
        self.s.command("margen 15 20 10 1")
        with self.assertRaises(ValueError):
            self.s.command("w 200")

    def test_measured_geometry(self):
        camera = dict(ancho=640,alto=360)
        self.assertEqual(measured_geometry(["314","340:50","278:100","220:150"],camera)["cx_garra"],314)
        for invalid in (["640","340:50","278:100","220:150"],["310","220:50","278:100","340:150"]):
            with self.assertRaises(ValueError):
                measured_geometry(invalid,camera)

    def test_floor_stops_and_latches(self):
        for sample in (reading("DER","negro"),reading("IZQ","blanco"),reading("IZQ","azul",1.8),
                       "TCS DER ERROR",reading("DER","azul")+" SATURADO"):
            with self.subTest(sample=sample):
                self.s.port = Port([sample])
                now = iter(i*.01 for i in range(5000))
                with patch("pista_uvm.time.monotonic",side_effect=lambda:next(now)):
                    self.s.command("margen 80 20 10 1")
                    self.s.command("w 250")
                commands = self.s.port.commands
                self.assertEqual(commands[commands.index("m 80 80 250")+1],"p")
                self.assertEqual(self.s.free,0)

    def test_loss_of_tcs_stream(self):
        self.s.port = Port([])
        now = iter(i*.01 for i in range(5000))
        with patch("pista_uvm.time.monotonic",side_effect=lambda:next(now)):
            self.s.command("margen 80 20 10 1")
            self.s.command("w 750")
        self.assertEqual(self.s.free,0)


if __name__ == "__main__":
    unittest.main()
