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

    def test_backoff_then_delivery(self):
        self.add()
        self.box.deliver(self.box.claim(0)[0],0,"failure")
        self.assertEqual(self.box.claim(4),[])
        retry = self.box.claim(5)[0]
        self.assertEqual(retry["attempts"],2)
        self.box.deliver(retry,5)
        self.assertEqual(self.box.snapshot()["messages"][0]["status"],"delivered")

    def test_bounded_retries_dead_letter(self):
        self.add()
        for now in [0,5,15]: self.box.deliver(self.box.claim(now)[0],now,"failure")
        row = self.box.snapshot()["messages"][0]
        self.assertEqual((row["status"],row["attempts"]),("dead",3))
        self.assertEqual(self.box.claim(100),[])

    def test_crash_after_accept_recovers_without_duplicate(self):
        self.add()
        self.box.deliver(self.box.claim(0)[0],0,"accepted_then_crash")
        self.assertEqual(self.box.snapshot()["messages"][0]["status"],"processing")
        self.assertEqual(self.box.claim(10),[])
        result = self.box.snapshot()
        self.assertEqual(result["messages"][0]["status"],"delivered")
        self.assertEqual(len(result["receipts"]),1)

    def test_expired_claim_is_reissued_and_old_worker_refused(self):
        self.add()
        old = self.box.claim(0)[0]
        new = self.box.claim(10)[0]
        with self.assertRaises(ValueError): self.box.deliver(old,10)
        self.box.deliver(new,10)

    def test_two_connections_do_not_claim_same_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/"outbox.sqlite")
            first, second = Outbox(path), Outbox(path)
            try:
                first.enqueue("a", {"demo":1})
                self.assertEqual(len(first.claim(0)),1)
                self.assertEqual(second.claim(0),[])
            finally:
                first.close(); second.close()

    def test_expiry_after_final_unacknowledged_attempt_is_dead(self):
        box = Outbox(max_attempts=1)
        try:
            box.enqueue("a", {})
            box.claim(0)
            self.assertEqual(box.claim(10),[])
            self.assertEqual(box.snapshot()["messages"][0]["status"],"dead")
        finally: box.close()

    def test_reduced_retry_limit_dead_letters_pending_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/"outbox.sqlite")
            first = Outbox(path, max_attempts=5)
            try:
                first.enqueue("exhausted", {})
                for now in [0,5,15]:
                    first.deliver(first.claim(now)[0],now,"failure")
                self.assertEqual(first.snapshot()["messages"][0]["status"],"pending")
            finally: first.close()

            restarted = Outbox(path, max_attempts=3)
            try:
                restarted.enqueue("fresh", {})
                claimed = restarted.claim(100)
                self.assertEqual([message["id"] for message in claimed],["fresh"])
                restarted.deliver(claimed[0],100)
                messages = {row["id"]: row for row in restarted.snapshot()["messages"]}
                self.assertEqual((messages["exhausted"]["status"],messages["exhausted"]["attempts"]),("dead",3))
                self.assertEqual(messages["exhausted"]["last_error"],"retry limit reached before claim")
                self.assertEqual(messages["fresh"]["status"],"delivered")
                self.assertEqual(restarted.claim(200),[])
            finally: restarted.close()

    def test_reduced_retry_limit_preserves_active_lease_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/"outbox.sqlite")
            first = Outbox(path, max_attempts=5)
            try:
                first.enqueue("a", {})
                first.deliver(first.claim(0)[0],0,"failure")
                active = first.claim(5,lease_seconds=20)[0]
            finally: first.close()

            restarted = Outbox(path, max_attempts=1)
            try:
                self.assertEqual(restarted.claim(10),[])
                self.assertEqual(restarted.snapshot()["messages"][0]["status"],"processing")
                self.assertEqual(restarted.deliver(active,10),"delivered")
                self.assertEqual(len(restarted.snapshot()["receipts"]),1)
            finally: restarted.close()

    def test_invalid_schedule_rejected(self):
        with self.assertRaises(ValueError): simulate({"messages":[],"times":[10,0]})


if __name__ == "__main__": unittest.main()
