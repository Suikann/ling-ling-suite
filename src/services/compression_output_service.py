# -*- coding: utf-8 -*-
"""
壓縮的批次與輸出位置

把要壓縮的 PDF（多選同一資料夾的檔、整個資料夾連同子資料夾，或另存單一檔）排成一批，
決定各份交到輸出位置的哪裡、擋下會蓋掉原檔的位置、列出會被覆蓋的檔，
一次一份壓完整批後再一起交出；取消時已壓好的全部丟掉、什麼都不交出。
一批輸出的檔名不加後綴、照原本的子資料夾結構，所以輸入的資料夾（含子資料夾）一律不能當輸出位置；
另存單一檔時檔名由使用者決定，只擋原檔本身。
壓縮一份 PDF 在 compression_service，這裡只管批次與輸出，檔案存取經過 FileService。

使用範例：
    output = CompressionOutputService(FileService(), CompressionService(FileService()))
    sources = output.collect_folder("D:/Symphony")
    batch = output.plan("D:/Symphony", sources, output.default_output("D:/Symphony"))  # D:/Symphony（壓縮）
    replaced = output.overwritten(batch)  # 先請使用者確認覆蓋
    results = output.compress(batch)  # 取消時為 None
    if results is not None:
        errors = output.deliver(batch, results)
        output.discard(results)
"""
import os
from dataclasses import dataclass
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from core.constants import COMPRESSED_FOLDER_SUFFIX, COMPRESSED_FOLDER_SUFFIX_EN, CompressionStatus
from core.locale import localized
from core.paths import is_inside, path_key, same_path
from services.compression_service import CompressionProgress, CompressionResult, CompressionService
from services.file_service import FileService


@dataclass(frozen=True)
class CompressionJob:
    """一批裡的一份：原檔與它在輸出位置的檔案

    Attributes:
        source: 原檔路徑
        target: 交出時寫到的檔案路徑
        name: 在輸出位置裡的相對路徑（列給使用者看的檔名）
    """
    source: str
    target: str
    name: str


@dataclass(frozen=True)
class CompressionBatch:
    """要一起壓縮、一起交出的一批 PDF

    Attributes:
        root: 輸入的資料夾（多選檔時是它們所在的資料夾）；一批的輸出位置不能在它裡面
        output: 輸出位置：一批時是資料夾，單一檔另存時是那個檔案
        jobs: 各份，依原檔的路徑排序
        single: 是否為另存單一檔（只擋原檔本身，見 is_single_blocked）；否則照一批的規則（見 is_blocked）
    """
    root: str
    output: str
    jobs: Tuple[CompressionJob, ...]
    single: bool = False


class BatchProgress(NamedTuple):
    """一批的壓縮進度：正在壓第幾份、共幾份，以及該份的壓縮進度

    Attributes:
        index: 正在壓第幾份（從 1 起）
        total: 共幾份
        source: 這一份的原檔路徑
        step: 這一份的壓縮進度；剛開始這一份時為 None
    """
    index: int
    total: int
    source: str
    step: Optional[CompressionProgress]


class CompressionOutputService:
    """壓縮的批次、輸出位置規則與交出"""

    def __init__(self, file_service: FileService, compressor: CompressionService):
        """
        Args:
            file_service: 檔案服務
            compressor: 壓縮一份 PDF 的服務
        """
        self._files = file_service
        self._compressor = compressor

    def collect_folder(self, folder: str) -> List[str]:
        """列出資料夾裡（含各層子資料夾）的 PDF

        Args:
            folder: 輸入的資料夾

        Returns:
            PDF 的完整路徑；同一層先列檔、再依名稱進子資料夾

        Raises:
            OSError: 資料夾讀不到
        """
        sources = self._files.list_pdf_files(folder)
        for sub in self._files.list_subdirectories(folder):
            sources.extend(self.collect_folder(sub))
        return sources

    @staticmethod
    def default_output(root: str) -> str:
        """一批的預設輸出位置：輸入的資料夾旁的「<資料夾名>（壓縮）」

        Args:
            root: 輸入的資料夾

        Returns:
            輸出位置的資料夾
        """
        suffix = localized(COMPRESSED_FOLDER_SUFFIX, COMPRESSED_FOLDER_SUFFIX_EN)
        return os.path.normpath(os.path.abspath(root)) + suffix

    @staticmethod
    def input_root(sources: Sequence[str]) -> str:
        """多選的檔所在的資料夾，即這一批輸入的資料夾

        Args:
            sources: 原檔路徑，至少一個

        Returns:
            它們共同所在的資料夾

        Raises:
            ValueError: 原檔不在同一個資料夾
        """
        folders = {path_key(os.path.dirname(os.path.abspath(source))) for source in sources}
        if len(folders) != 1:
            raise ValueError("多選的檔要在同一個資料夾")
        return os.path.dirname(os.path.abspath(sources[0]))

    def plan(self, root: str, sources: Sequence[str], output: str) -> CompressionBatch:
        """把一批原檔對應到輸出位置裡同樣的相對路徑，檔名不加後綴

        Args:
            root: 輸入的資料夾，各原檔都在它裡面
            sources: 原檔路徑
            output: 輸出位置的資料夾

        Returns:
            這一批

        Raises:
            ValueError: 輸出位置被擋下（見 is_blocked）
        """
        if self.is_blocked(root, output):
            raise ValueError(f"輸出位置在輸入的資料夾裡，會蓋掉原檔：{output}")
        jobs = []
        for source in sources:
            name = os.path.relpath(os.path.abspath(source), os.path.abspath(root))
            jobs.append(CompressionJob(source, os.path.join(output, name), name))
        return CompressionBatch(root, output, tuple(jobs))

    @classmethod
    def default_target(cls, source: str) -> str:
        """單一檔的預設另存位置：原資料夾旁的「<原資料夾名>（壓縮）」，檔名不變

        Args:
            source: 原檔路徑

        Returns:
            另存的檔案路徑
        """
        return os.path.join(cls.default_output(cls.input_root([source])), os.path.basename(source))

    @staticmethod
    def is_blocked(root: str, location: str) -> bool:
        """一批的輸出位置會不會蓋掉原檔：是輸入的資料夾、或在它裡面（任意深度）就擋下

        一批輸出的檔名不加後綴，所以輸入的資料夾裡一律不能當輸出位置。

        Args:
            root: 輸入的資料夾
            location: 輸出位置的資料夾，或要交到的檔案

        Returns:
            要擋下時為 True
        """
        return same_path(location, root) or is_inside(location, root)

    @staticmethod
    def is_single_blocked(source: str, target: str) -> bool:
        """另存單一檔的位置會不會蓋掉原檔：選到原檔本身（不分大小寫、不論寫法）才擋下

        Args:
            source: 原檔路徑
            target: 另存的檔案路徑

        Returns:
            要擋下時為 True
        """
        return same_path(target, source)

    def plan_single(self, source: str, target: str) -> CompressionBatch:
        """單一檔另存成一批只有一份：輸入的資料夾是原檔所在的資料夾，交到使用者選的檔名

        Args:
            source: 原檔路徑
            target: 另存的檔案路徑；原檔所在的資料夾裡、換個檔名也可以

        Returns:
            只有一份的一批

        Raises:
            ValueError: 另存位置是原檔本身（見 is_single_blocked）
        """
        if self.is_single_blocked(source, target):
            raise ValueError(f"另存位置是原檔，會蓋掉原檔：{target}")
        job = CompressionJob(source, target, os.path.basename(target))
        return CompressionBatch(self.input_root([source]), target, (job,), single=True)

    def overwritten(self, batch: CompressionBatch) -> List[CompressionJob]:
        """輸出位置已有同名檔的那幾份（例如上次的壓縮結果），交出時會被覆蓋

        Args:
            batch: 這一批

        Returns:
            會覆蓋既有檔案的各份，與 batch.jobs 同序
        """
        return [job for job in batch.jobs if self._files.file_exists(job.target)]

    def compress(
        self, batch: CompressionBatch, should_cancel: Callable[[], bool] = lambda: False,
        on_progress: Callable[[BatchProgress], None] = lambda progress: None,
    ) -> Optional[Tuple[CompressionResult, ...]]:
        """一次一份壓縮整批，壓縮檔留在暫存資料夾，等整批交出（或丟掉）

        可以在背景執行緒呼叫；取消在頁與頁之間生效，已壓好的也全部丟掉，暫存資料夾裡什麼都不留。

        Args:
            batch: 這一批
            should_cancel: 頁與頁之間呼叫，回傳 True 就取消整批
            on_progress: 每一份開始時回報一次（step 為 None），之後轉回報該份的壓縮進度

        Returns:
            各份的壓縮結果，與 batch.jobs 同序；取消時為 None
        """
        results = []
        total = len(batch.jobs)
        try:
            for index, job in enumerate(batch.jobs, start=1):
                on_progress(BatchProgress(index, total, job.source, None))
                if should_cancel():
                    break
                result = self._compressor.compress(
                    job.source, should_cancel,
                    lambda step, index=index, job=job: on_progress(BatchProgress(index, total, job.source, step)),
                )
                results.append(result)
                if result.status is CompressionStatus.CANCELLED:
                    break
            else:
                return tuple(results)
        except BaseException:
            self.discard(results)
            raise
        self.discard(results)
        return None

    def deliver(self, batch: CompressionBatch, results: Sequence[CompressionResult]) -> Dict[str, OSError]:
        """把結果交到各份在輸出位置的檔案；目的地已有檔案時覆蓋

        不到上限的原檔原樣複製，帶有壓縮檔的複製壓縮檔；壓縮失敗與無法壓縮的不交出。
        只交 results 裡有的那幾份，所以呼叫端可以先挑掉不要的。暫存的壓縮檔留著，由呼叫端以 discard 清掉。

        Args:
            batch: 這一批
            results: 要交出的結果（batch 裡各份的一部分或全部）

        Returns:
            複製失敗的原檔路徑對應到錯誤；全部成功時為空

        Raises:
            ValueError: 有一份的目的地被擋下（見 is_blocked、is_single_blocked），此時什麼都不交出
        """
        targets = {path_key(job.source): job.target for job in batch.jobs}
        pending = []
        for result in results:
            target = targets[path_key(result.source)]
            if self._target_blocked(batch, result.source, target):
                raise ValueError(f"輸出位置會蓋掉原檔，拒絕交出：{target}")
            if result.status is CompressionStatus.SMALL_ENOUGH:
                pending.append((result.source, result.source, target))
            elif result.output_path is not None:
                pending.append((result.source, result.output_path, target))
        errors = {}
        for source, copied, target in pending:
            try:
                self._files.create_directory(os.path.dirname(os.path.abspath(target)))
                self._files.copy_file(copied, target)
            except OSError as e:
                errors[source] = e
        return errors

    def _target_blocked(self, batch: CompressionBatch, source: str, target: str) -> bool:
        """交出前再確認一次目的地不會蓋掉原檔：另存單一檔照 is_single_blocked，一批照 is_blocked"""
        if batch.single:
            return self.is_single_blocked(source, target)
        return self.is_blocked(batch.root, target)

    def discard(self, results: Sequence[CompressionResult]) -> None:
        """丟掉各結果在暫存資料夾裡的壓縮檔（交出之後、取消、或使用者不要時）"""
        for result in results:
            self._compressor.discard(result)
