import os,time
from django.core.management.base import BaseCommand
from shop.services.source_sync import sync_all
class Command(BaseCommand):
    def handle(self,*args,**opts):
        interval=max(300,int(os.getenv("SOURCE_SYNC_INTERVAL","1800")))
        while True:
            self.stdout.write("Syncing linked products...")
            try: sync_all()
            except Exception as e: self.stderr.write(str(e))
            time.sleep(interval)
