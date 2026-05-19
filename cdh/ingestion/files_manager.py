"""
cdh.ingestion.files_manager
============================

Date-range utilities and folder management for raw climate file downloads.
"""

from __future__ import annotations

import glob
import itertools
import os
import re
import zipfile
from calendar import monthrange
from typing import Dict, List, Tuple

import numpy as np
from dateutil.parser import parse


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def split_date(date: str) -> Tuple[int, int, int]:
    """Split 'YYYY-MM-DD' into (year, month, day)."""
    if "-" in date:
        year, month, day = tuple(map(int, date.split("-")))
    assert month <= 12
    return [year, month, day]


def months_range_asstring(month_init: int, month_end: int) -> List[str]:
    if month_end > 12:
        month_end = 12
    if month_init < 1:
        month_init = 1
    if month_init != month_end:
        months = [f"{i}" if i > 9 else f"0{i}" for i in range(month_init, month_end + 1)]
    else:
        months = [f"{month_init}" if month_init > 9 else f"0{month_init}"]
    return months


def days_range_asstring(day_init: int, day_end: int) -> List[str]:
    if day_end > 31:
        day_end = 31
    if day_init < 1:
        day_init = 1
    return [f"{i}" if i > 9 else f"0{i}" for i in range(day_init, day_end + 1)]


def set_months_and_days(
    year: int,
    init_month: int,
    end_month: int,
    init_day: int | None = None,
    end_day: int | None = None,
) -> Dict[str, List[str]]:
    months = months_range_asstring(init_month, end_month)
    month_dict: Dict[str, List[str]] = {}
    for month in months:
        if int(month) == end_month:
            end_dayc = monthrange(year, int(month))[1] if end_day is None else end_day
        else:
            end_dayc = monthrange(year, int(month))[1]
        if int(month) == init_month:
            init_dayc = 1 if init_day is None else init_day
        else:
            init_dayc = 1
        month_dict[month] = days_range_asstring(init_dayc, end_dayc)
    return month_dict


def create_yearly_query(init_date: str, end_date: str) -> Dict[str, Dict[str, List[str]]]:
    """Return a nested dict {year: {month: [day, ...]}} for the date range."""
    sty, stm, std = split_date(init_date)
    eny, enm, end = split_date(end_date)
    diffyears = eny - sty
    queryyearlydates: Dict[str, Dict] = {}
    if diffyears != 0:
        for year in range(sty, eny + 1):
            if year == sty:
                month_days = set_months_and_days(year=year, init_day=std, init_month=stm, end_month=12)
            elif year == eny:
                month_days = set_months_and_days(year=year, init_month=1, end_month=enm, end_day=end)
            else:
                month_days = set_months_and_days(year=year, init_month=1, end_month=12)
            queryyearlydates[str(year)] = {i: month_days[i] for i in month_days}
    else:
        month_days = set_months_and_days(year=sty, init_day=std, init_month=stm, end_month=enm, end_day=end)
        queryyearlydates[str(sty)] = {i: month_days[i] for i in month_days}
    return queryyearlydates


def concatenate_dates(year: str, dict_dates: Dict, sep: str = "") -> List[str]:
    cdates = []
    for month in dict_dates[year]:
        for day in dict_dates[year][month]:
            cdates.append(f"{year}{sep}{month}{sep}{day}")
    return cdates


# ---------------------------------------------------------------------------
# Zip / folder helpers
# ---------------------------------------------------------------------------

def check_filesinzipfolder(folder: list | str) -> Dict:
    folder = folder if isinstance(folder, list) else [folder]
    zipfolder = [i for i in folder if i.endswith(".zip")]
    out: Dict = {}
    if len(zipfolder) == 1:
        out["inputfolder"] = zipfolder[0]
        out["tempfolder"] = zipfolder[0][: zipfolder[0].index(".zip")]
        out["unzip"] = True
    else:
        out["inputfolder"] = folder[0]
        out["unzip"] = False
    return out


def uncompress_zip_path(path: str, year: str) -> str:
    foldermanager = check_filesinzipfolder(glob.glob(path + f"/*{year}*"))
    if foldermanager["unzip"]:
        if not os.path.exists(foldermanager["tempfolder"]):
            with zipfile.ZipFile(foldermanager["inputfolder"], "r") as zip_ref:
                zip_ref.extractall(foldermanager["tempfolder"])
        return foldermanager["tempfolder"]
    return foldermanager["inputfolder"]


# ---------------------------------------------------------------------------
# Date-string detection
# ---------------------------------------------------------------------------

def is_date(string: str, fuzzy: bool = False) -> bool:
    try:
        parse(string, fuzzy=fuzzy)
        return True
    except ValueError:
        return False


def find_date_instring(string: str, pattern: str = "202", yearformat: str = "yyyy") -> str:
    matches = re.finditer(pattern, string)
    datelen = 8 if yearformat == "yyyy" else 6
    matches_positions = [
        string[m.start(): m.start() + datelen]
        for m in matches
        if is_date(string[m.start(): m.start() + datelen])
    ]
    if matches_positions and len(matches_positions[0]) == 6:
        matches_positions = [pattern[:-1] + matches_positions[0]]
    return matches_positions[0]


# ---------------------------------------------------------------------------
# IntervalFolderManager
# ---------------------------------------------------------------------------

class IntervalFolderManager:
    """Discover date-file pairs inside a downloaded variable folder."""

    @staticmethod
    def split_date(date: str) -> Tuple[int, int, int]:
        return split_date(date)

    @property
    def query_dates(self) -> Dict:
        if self._query_dates is None:
            self._query_dates = create_yearly_query(
                init_date=self.starting_date, end_date=self.ending_date
            )
        return self._query_dates

    def range_years(self) -> List[int]:
        return list(range(self._ys, self._ye + 1))

    def __init__(self) -> None:
        self._folders_to_remove: List[str] = []
        self._query_dates: Dict | None = None
        self.path = ""
        self.starting_date = ""
        self.ending_date = ""
        self._ys = self._ms = self._ds = 0
        self._ye = self._me = self._de = 0

    def check_and_extract_zip(self, year: str, extension: str = ".zip") -> str:
        if extension == ".zip":
            return uncompress_zip_path(self.path, year)
        return os.path.join(self.path, year)

    def check_path_exists(self) -> None:
        assert os.path.exists(self.path), f"Path does not exist: {self.path}"

    def split_dates(self) -> None:
        self._ys, self._ms, self._ds = self.split_date(self.starting_date)
        self._ye, self._me, self._de = self.split_date(self.ending_date)

    def initialize(self, path: str, starting_date: str, ending_date: str) -> None:
        self.path = path
        self.check_path_exists()
        self.starting_date = starting_date
        self.ending_date = ending_date
        self.split_dates()
        self._query_dates = None  # reset cache

    def __call__(
        self, path: str, starting_date: str, ending_date: str
    ) -> List[List[str]]:
        self.initialize(path, starting_date, ending_date)
        listfilesyear = []
        _VALID_EXTS = (".nc", ".tif", ".tiff", ".zip")
        for year in self.range_years():
            folder_path = self.check_and_extract_zip(str(year))
            dates_toquery = concatenate_dates(str(year), self.query_dates)
            file_names = [
                [d, filepath]
                for filepath in os.listdir(folder_path)
                for d in dates_toquery
                if filepath.find(d) != -1 and filepath.lower().endswith(_VALID_EXTS)
            ]
            listfilesyear.append(file_names)
        return list(itertools.chain.from_iterable(listfilesyear))
