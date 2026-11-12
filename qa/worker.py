"""Worker process for concurrency / crash / response-loss evidence.

Invoked as a real OS process so the parent can observe call/return windows,
kill it mid-apply, reopen the on-disk database and replay receipts. It never
imports the QA oracle; it only drives the public Store API.

Subcommands print one JSON line per observable event to stdout, prefixed so
the parent can reconstruct timing. A ready/handshake file is used for the
fault stages that must pause until the parent signals.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from merge import Error, Store


def emit(kind, **payload):
    record = {"event": kind}
    record.update(payload)
    print(json.dumps(record, ensure_ascii=False), flush=True)


def make_fault(stage_target, handshake_dir=None):
    def fault(stage):
        if stage != stage_target:
            return
        emit("fault_reached", stage=stage, pid=os.getpid())
        if handshake_dir:
            gate = os.path.join(handshake_dir, f"{stage_target}.gate")
            # Create the reached marker, then block until the parent removes
            # the gate file (or kills this process).
            open(gate + ".reached", "w").close()
            while os.path.exists(gate):
                try:
                    os.utime(gate, None)
                except OSError:
                    pass
                import time
                time.sleep(0.02)
        else:
            raise RuntimeError(f"injected-{stage_target}")
    return fault


def main():
    command = sys.argv[1]
    db_path = sys.argv[2]
    store = Store(db_path)

    if command == "apply":
        batch_id = sys.argv[3]
        generation = int(sys.argv[4])
        events_file = sys.argv[5]
        stage = sys.argv[6] if len(sys.argv) > 6 else ""
        handshake = sys.argv[7] if len(sys.argv) > 7 else ""
        with open(events_file, encoding="utf-8") as handle:
            events = json.load(handle)
        fault = make_fault(stage, handshake or None) if stage else None
        emit("call_start", batch_id=batch_id, generation=generation,
             pid=os.getpid())
        try:
            receipt = store.apply(batch_id, generation, events, fault=fault)
            emit("call_return", receipt=receipt)
        except BaseException as exc:  # include injected RuntimeError
            emit("call_error", marker=type(exc).__name__, message=str(exc))
        finally:
            store.close()

    elif command == "read":
        generation = sys.argv[3] if len(sys.argv) > 3 else "head"
        try:
            if generation == "head":
                emit("read", result=store.read())
            else:
                emit("read", result=store.read(int(generation)))
        except Error as exc:
            emit("read_error", marker=str(exc))
        finally:
            store.close()

    elif command == "audit":
        try:
            emit("audit", result=store.audit())
        finally:
            store.close()

    elif command == "replay":
        batch_id = sys.argv[3]
        generation = int(sys.argv[4])
        events_file = sys.argv[5]
        with open(events_file, encoding="utf-8") as handle:
            events = json.load(handle)
        emit("replay_start", batch_id=batch_id, pid=os.getpid())
        try:
            receipt = store.apply(batch_id, generation, events)
            emit("replay_return", receipt=receipt)
        except Error as exc:
            emit("replay_error", marker=str(exc))
        finally:
            store.close()


if __name__ == "__main__":
    main()
