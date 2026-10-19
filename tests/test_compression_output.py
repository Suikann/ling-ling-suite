# -*- coding: utf-8 -*-
"""
壓縮輸出位置的規則測試（臨時目錄）

預設位置是原資料夾旁的「<原資料夾名>（壓縮）」、檔名不變；一批時輸入的資料夾內都擋下，另存單一檔時只擋原檔本身；
不到上限的檔原樣複製，壓縮過的交出暫存的壓縮檔。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.constants import CompressionStatus
from services.compression_output_service import CompressionBatch, CompressionJob, CompressionOutputService
from services.compression_service import CompressionService
from services.file_service import FileService
from tests.path_spellings import spellings
from tests.synthetic_pdfs import encrypted_pdf, file_digest, scanned_pdf

KB = 1000


class TestCompressionOutput(unittest.TestCase):
    """單一檔的另存位置與交出"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.parts = os.path.join(self.temp_dir, "Symphony")
        os.makedirs(self.parts)
        self.source = scanned_pdf(os.path.join(self.parts, "Flute.pdf"), dpi=600)
        self.output = CompressionOutputService(FileService(), self.compressor(size_limit=10 ** 9))

    def compressor(self, size_limit: int) -> CompressionService:
        """暫存資料夾在臨時目錄、指定大小上限的壓縮服務"""
        return CompressionService(FileService(), os.path.join(self.temp_dir, "scratch"), size_limit=size_limit)

    def test_default_target_is_beside_the_original_folder_with_the_same_file_name(self):
        self.assertEqual(
            self.output.default_target(self.source),
            os.path.join(self.temp_dir, "Symphony（壓縮）", "Flute.pdf"),
        )

    def root(self) -> str:
        """原檔所在的資料夾"""
        return self.output.input_root([self.source])

    def test_only_the_original_file_itself_is_blocked(self):
        for target in (self.source, *spellings(self.source).values()):
            with self.subTest(target=target):
                self.assertTrue(self.output.is_single_blocked(self.source, target))
                with self.assertRaises(ValueError):
                    self.output.plan_single(self.source, target)

    def test_another_name_in_the_original_folder_and_other_folders_are_allowed(self):
        for target in (
            os.path.join(self.parts, "Flute small.pdf"),
            os.path.join(self.parts, "sub", "Flute.pdf"),
            self.output.default_target(self.source),
            os.path.join(self.temp_dir, "Symphony2", "Flute.pdf"),
            os.path.join(self.temp_dir, "Flute.pdf"),
        ):
            with self.subTest(target=target):
                self.assertFalse(self.output.is_single_blocked(self.source, target))
                self.assertEqual(self.output.plan_single(self.source, target).jobs[0].target, target)

    def test_single_file_is_a_batch_of_one_saved_under_the_chosen_name(self):
        target = os.path.join(self.temp_dir, "out", "Flute small.pdf")
        batch = self.output.plan_single(self.source, target)
        self.assertEqual([(job.source, job.target, job.name) for job in batch.jobs],
                         [(self.source, target, "Flute small.pdf")])

    def test_file_under_the_limit_is_copied_as_is(self):
        batch = self.output.plan_single(self.source, self.output.default_target(self.source))
        results = self.output.compress(batch)
        self.assertEqual([r.status for r in results], [CompressionStatus.SMALL_ENOUGH])
        self.assertEqual(self.output.deliver(batch, results), {})
        self.assertEqual(file_digest(batch.jobs[0].target), file_digest(self.source))

    def test_compressed_file_is_delivered_from_the_temp_folder(self):
        output = CompressionOutputService(FileService(), self.compressor(size_limit=500 * KB))
        target = os.path.join(self.temp_dir, "elsewhere", "Flute.pdf")
        batch = output.plan_single(self.source, target)
        results = output.compress(batch)
        self.assertEqual([r.status for r in results], [CompressionStatus.COMPRESSED])
        output.deliver(batch, results)
        self.assertEqual(file_digest(target), file_digest(results[0].output_path))
        output.discard(results)
        self.assertFalse(os.path.exists(results[0].output_path))
        self.assertTrue(os.path.isfile(target))

    def test_saving_under_another_name_beside_the_original_delivers_there(self):
        target = os.path.join(self.parts, "Flute small.pdf")
        batch = self.output.plan_single(self.source, target)
        results = self.output.compress(batch)
        self.assertEqual(self.output.deliver(batch, results), {})
        self.assertEqual(file_digest(target), file_digest(self.source))

    def test_delivering_a_single_file_onto_the_original_is_refused(self):
        output = CompressionOutputService(FileService(), self.compressor(size_limit=500 * KB))
        job = CompressionJob(self.source, self.source, "Flute.pdf")
        batch = CompressionBatch(self.parts, self.source, (job,), single=True)
        results = output.compress(batch)
        digest = file_digest(self.source)
        with self.assertRaises(ValueError):
            output.deliver(batch, results)
        output.discard(results)
        self.assertEqual(file_digest(self.source), digest)

    def test_delivering_to_a_blocked_target_is_refused(self):
        output = CompressionOutputService(FileService(), self.compressor(size_limit=500 * KB))
        batch = CompressionBatch(self.parts, self.source, (CompressionJob(self.source, self.source, "Flute.pdf"),))
        results = output.compress(batch)
        digest = file_digest(self.source)
        with self.assertRaises(ValueError):
            output.deliver(batch, results)
        output.discard(results)
        self.assertEqual(file_digest(self.source), digest)


class TestCompressionBatch(unittest.TestCase):
    """一整批：選整個資料夾或多選檔，輸出照原本的子資料夾結構"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.root = os.path.join(self.temp_dir, "Symphony")
        os.makedirs(os.path.join(self.root, "Strings"))
        self.flute = scanned_pdf(os.path.join(self.root, "Flute.pdf"), dpi=300)
        self.violin = scanned_pdf(os.path.join(self.root, "Strings", "Violin.pdf"), dpi=300)
        with open(os.path.join(self.root, "notes.txt"), "w") as f:
            f.write("not a pdf")
        self.scratch = os.path.join(self.temp_dir, "scratch")
        self.output = self.service(size_limit=10 ** 9)

    def service(self, size_limit: int) -> CompressionOutputService:
        """暫存資料夾在臨時目錄、指定大小上限的批次服務"""
        files = FileService()
        return CompressionOutputService(files, CompressionService(files, self.scratch, size_limit=size_limit))

    def test_folder_includes_pdfs_in_subfolders(self):
        self.assertEqual(self.output.collect_folder(self.root), [self.flute, self.violin])

    def test_default_output_is_beside_the_input_folder(self):
        self.assertEqual(self.output.default_output(self.root), os.path.join(self.temp_dir, "Symphony（壓縮）"))

    def test_targets_keep_the_subfolder_structure_and_file_names(self):
        out = os.path.join(self.temp_dir, "out")
        batch = self.output.plan(self.root, self.output.collect_folder(self.root), out)
        self.assertEqual(
            [(job.source, job.target, job.name) for job in batch.jobs],
            [
                (self.flute, os.path.join(out, "Flute.pdf"), "Flute.pdf"),
                (self.violin, os.path.join(out, "Strings", "Violin.pdf"), os.path.join("Strings", "Violin.pdf")),
            ],
        )

    def test_input_folder_and_anywhere_inside_it_are_blocked(self):
        for output in (self.root, os.path.join(self.root, "Strings"), os.path.join(self.root, "new"),
                       *spellings(self.root).values()):
            with self.subTest(output=output):
                self.assertTrue(self.output.is_blocked(self.root, output))
                with self.assertRaises(ValueError):
                    self.output.plan(self.root, [self.flute], output)

    @unittest.skipUnless(os.name == "nt", "只有 Windows 的路徑有磁碟代號")
    def test_output_on_another_drive_is_allowed(self):
        other = "Y:" if os.path.splitdrive(self.root)[0].upper() == "Z:" else "Z:"
        output = other + os.sep + "out"
        self.assertFalse(self.output.is_blocked(self.root, output))
        self.assertEqual(self.output.plan(self.root, [self.flute], output).jobs[0].target,
                         os.path.join(output, "Flute.pdf"))

    def test_default_output_and_the_parent_folder_are_allowed(self):
        for output in (self.output.default_output(self.root), self.temp_dir):
            with self.subTest(output=output):
                self.assertFalse(self.output.is_blocked(self.root, output))

    def test_lists_the_files_an_earlier_output_would_have_overwritten(self):
        out = self.output.default_output(self.root)
        os.makedirs(os.path.join(out, "Strings"))
        for name in (os.path.join("Strings", "Violin.pdf"), "Oboe.pdf"):
            with open(os.path.join(out, name), "wb") as f:
                f.write(b"earlier output")
        batch = self.output.plan(self.root, self.output.collect_folder(self.root), out)
        self.assertEqual([job.name for job in self.output.overwritten(batch)], [os.path.join("Strings", "Violin.pdf")])

    def test_whole_batch_is_delivered_with_small_files_copied_as_is(self):
        out = os.path.join(self.temp_dir, "out")
        batch = self.output.plan(self.root, self.output.collect_folder(self.root), out)
        self.assertEqual(self.output.deliver(batch, self.output.compress(batch)), {})
        self.assertEqual(file_digest(os.path.join(out, "Flute.pdf")), file_digest(self.flute))
        self.assertEqual(file_digest(os.path.join(out, "Strings", "Violin.pdf")), file_digest(self.violin))

    def test_multiselected_files_must_share_a_folder(self):
        self.assertEqual(self.output.input_root([self.flute, os.path.join(self.root, "Oboe.pdf")]), self.root)
        with self.assertRaises(ValueError):
            self.output.input_root([self.flute, self.violin])

    def scratch_files(self):
        """暫存資料夾裡留下的檔案"""
        return [name for _, _, names in os.walk(self.scratch) for name in names]

    def test_progress_reports_which_file_of_how_many(self):
        batch = self.output.plan(self.root, self.output.collect_folder(self.root), os.path.join(self.temp_dir, "out"))
        reported = []
        self.output.compress(batch, on_progress=reported.append)
        self.assertEqual([(p.index, p.total, p.source) for p in reported], [(1, 2, self.flute), (2, 2, self.violin)])

    def test_cancelling_after_a_compressed_file_discards_it_and_delivers_nothing(self):
        output = self.service(size_limit=100 * KB)
        batch = output.plan(self.root, output.collect_folder(self.root), os.path.join(self.temp_dir, "out"))
        reported = []
        results = output.compress(
            batch, should_cancel=lambda: any(p.index == 2 for p in reported), on_progress=reported.append,
        )
        self.assertIsNone(results)
        self.assertTrue(any(p.index == 1 and p.step is not None and p.step.checking for p in reported))
        self.assertEqual(self.scratch_files(), [])
        self.assertFalse(os.path.exists(batch.output))

    def test_files_that_cannot_be_compressed_are_not_delivered(self):
        encrypted_pdf(os.path.join(self.root, "Oboe.pdf"), dpi=300)
        output = self.service(size_limit=100 * KB)
        out = os.path.join(self.temp_dir, "out")
        batch = output.plan(self.root, output.collect_folder(self.root), out)
        results = output.compress(batch)
        self.assertEqual([r.status for r in results], [
            CompressionStatus.COMPRESSED, CompressionStatus.UNCOMPRESSIBLE, CompressionStatus.COMPRESSED,
        ])
        output.deliver(batch, results)
        output.discard(results)
        self.assertEqual(sorted(name for _, _, names in os.walk(out) for name in names), ["Flute.pdf", "Violin.pdf"])
        self.assertEqual(self.scratch_files(), [])


if __name__ == '__main__':
    unittest.main()
