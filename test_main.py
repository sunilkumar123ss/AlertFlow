from pathlib import Path
import tempfile
import unittest
from main import Outbox, simulate


class OutboxTests(unittest.TestCase):
    def setUp(self): self.box = Outbox()
    def tearDown(self): self.box.close()

    def add(self): self.box.enqueue("a", {"synthetic": True})

    def test_enqueue_idempotency_and_conflict(self):
        self.add(); self.add()
        self.assertEqual(len(self.box.snapshot()["messages"]),1)
        with self.assertRaises(ValueError): self.box.enqueue("a", {"different":True})

    def test_success_has_single_receipt(self):
        self.add()
        self.box.deliver(self.box.claim(0)[0],0)
        self.assertEqual(self.box.claim(1),[])
        self.assertEqual(len(self.box.snapshot()["receipts"]),1)
