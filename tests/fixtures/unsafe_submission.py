import os

from derivebench import Model, Signal, Tick


class ModelSubmission(Model):
    def on_tick(self, tick: Tick) -> Signal | None:
        os.system("echo unsafe")
        return None

