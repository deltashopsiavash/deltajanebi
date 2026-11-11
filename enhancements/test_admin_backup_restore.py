"""Regression: browser backup restore requires superuser, CSRF, explicit confirmation."""
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from shop.models import User


class BrowserBackupRestoreTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_superuser("owner@example.com", "pass12345")
        cls.staff = User.objects.create_user("staff@example.com", "pass12345", is_staff=True)

    def setUp(self):
        self.url = reverse("delta_backup_restore_admin")

    def test_login_and_superuser_required(self):
        assert self.client.get(self.url).status_code == 302
        self.client.force_login(self.staff)
        assert self.client.get(self.url).status_code == 403

    def test_confirm_required_and_bad_zip_rejected(self):
        self.client.force_login(self.owner)
        assert self.client.get(self.url).status_code == 200
        file = SimpleUploadedFile("old.deltabackup", b"not-a-zip")
        with patch("enhancements.admin_backup_restore.restore_backup_archive") as restore:
            assert self.client.post(self.url, {"backup_file": file}).status_code == 400
            restore.assert_not_called()
            file2 = SimpleUploadedFile("old.deltabackup", b"not-a-zip")
            assert self.client.post(self.url, {"backup_file": file2, "confirm_restore": "yes"}).status_code == 400
            restore.assert_not_called()

    def test_csrf_check(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        file = SimpleUploadedFile("old.deltabackup", b"test")
        assert client.post(self.url, {"backup_file": file, "confirm_restore": "yes"}).status_code == 403

    def test_valid_archive_is_streamed_validated_and_cleaned(self):
        self.client.force_login(self.owner)
        with TemporaryDirectory() as root:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as arc:
                arc.writestr("manifest.json", "{}")
                arc.writestr("database.json", "[]")
            with patch("enhancements.admin_backup_restore.BACKUP_DIR", Path(root)), \
                 patch("enhancements.admin_backup_restore.validate_backup_archive", return_value={"created_at": "2026-08-28"}) as check, \
                 patch("enhancements.admin_backup_restore.restore_backup_archive", return_value=Path(root) / "before-restore.deltabackup") as restore:
                file = SimpleUploadedFile("old.deltabackup", buf.getvalue())
                response = self.client.post(self.url, {"backup_file": file, "confirm_restore": "yes"})
                assert response.status_code == 200
                check.assert_called_once()
                restore.assert_called_once()
                assert list(Path(root).glob(".delta-admin-upload-*")) == []
