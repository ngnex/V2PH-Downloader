import os

# os.environ["GITHUB_ACTIONS"] = "true"
import time
import shutil
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from v2dl.common import BaseConfig, RuntimeConfig
from v2dl.core import ScrapeHandler, ScrapeManager
from v2dl.utils import DownloadStatus, ServiceType
from v2dl.web_bot import get_bot

TEST_ALBUM_URL = "http://example.com/album"


@pytest.fixture
def setup_test_env(tmp_path, real_base_config, real_runtime_config):
    def setup_env(service_type, log_level):
        runtime_config = real_runtime_config(service_type, log_level)
        web_bot = get_bot(runtime_config, real_base_config)
        scraper = ScrapeHandler(runtime_config, real_base_config, web_bot)

        return scraper, real_base_config, runtime_config

    try:
        yield setup_env
    finally:
        download_dir = tmp_path / "Downloads"
        download_log = tmp_path / "download.log"
        if download_dir.exists():
            shutil.rmtree(download_dir)
        if download_log.exists():
            download_log.unlink()


@pytest.fixture
def mock_runtime_config():
    runtime_config = MagicMock()
    runtime_config.url = TEST_ALBUM_URL
    runtime_config.language = "en"
    runtime_config.dry_run = False
    runtime_config.logger = logging.getLogger()
    runtime_config.download_service = MagicMock()
    runtime_config.download_function = MagicMock()
    runtime_config.input_file = None
    runtime_config.force_download = False
    runtime_config.log_level = logging.DEBUG
    return runtime_config


@pytest.fixture
def mock_base_config(tmp_path):
    base_config = MagicMock()
    base_config.paths.download_log = tmp_path / "mock_log_path"
    return base_config


@pytest.fixture
def mock_web_bot():
    web_bot = MagicMock()
    web_bot.close_driver = MagicMock()
    web_bot.auto_page_scroll = MagicMock(return_value="<html></html>")
    return web_bot


@pytest.fixture
def real_scrape_manager(mock_runtime_config, mock_base_config, mock_web_bot):
    return ScrapeManager(mock_runtime_config, mock_base_config, mock_web_bot)


@pytest.fixture
def real_scrape_handler(mock_runtime_config, mock_base_config, mock_web_bot):
    return ScrapeHandler(mock_runtime_config, mock_base_config, mock_web_bot)


@pytest.mark.skipif(os.getenv("GITHUB_ACTIONS") == "true", reason="No GUI on Github")
def test_download(setup_test_env, real_args):
    scraper: ScrapeHandler
    base_config: BaseConfig
    runtime_config: RuntimeConfig
    valid_extensions = (".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG")

    setup_env = setup_test_env
    scraper, base_config, runtime_config = setup_env(ServiceType.ASYNC, logging.DEBUG)
    _, expected_file_count = real_args
    test_download_dir = Path(base_config.download.download_dir)

    scraper.scrape(runtime_config.url)
    runtime_config.download_service.stop(30)
    time.sleep(10)

    # Check directory
    subdirectories = [d for d in test_download_dir.iterdir() if d.is_dir()]
    download_subdir = subdirectories[0]
    assert download_subdir.is_dir(), "Expected a directory but found a file"

    # Check number of files
    image_files = sorted(download_subdir.glob("*"), key=lambda x: x.name)
    image_files = [f for f in image_files if f.suffix.lower() in valid_extensions]
    assert len(image_files) == expected_file_count, (
        f"Expected {expected_file_count} images, found {len(image_files)}"
    )

    # Check file names match 001, 002, 003...
    for idx, image_file in enumerate(image_files, start=1):
        expected_filename = f"{idx:03d}"
        actual_filename = image_file.stem
        assert expected_filename == actual_filename, (
            f"Expected file name {expected_filename}, found {actual_filename}"
        )

    # Verify image file size
    for image_file in image_files:
        assert image_file.stat().st_size > 0, f"Downloaded image {image_file.name} is empty"


# ===================== Test ScrapeManager =====================


def test_load_urls(mock_runtime_config, real_scrape_manager, tmp_path):
    scrape_manager = real_scrape_manager
    test_file = tmp_path / "input_urls.txt"
    test_file.write_text(f"{TEST_ALBUM_URL}1\n{TEST_ALBUM_URL}2\n")

    mock_runtime_config.input_file = str(test_file)
    urls = scrape_manager._load_urls()
    assert urls == [TEST_ALBUM_URL + "1", TEST_ALBUM_URL + "2"]

    scrape_manager.runtime_config.url = TEST_ALBUM_URL
    scrape_manager.runtime_config.input_file = None
    urls = scrape_manager._load_urls()
    assert urls == [TEST_ALBUM_URL]

    if os.name != "nt":
        shutil.rmtree(tmp_path)


def test_start_scraping(real_scrape_manager):
    real_scrape_manager._load_urls = MagicMock(return_value=[TEST_ALBUM_URL])
    real_scrape_manager.scrape_handler.scrape = MagicMock()

    with pytest.raises(TypeError):
        real_scrape_manager.start_scraping()

    real_scrape_manager._load_urls.assert_called_once()
    real_scrape_manager.runtime_config.download_service.stop.assert_called_once()
    real_scrape_manager.web_bot.close_driver.assert_called_once()


def test_get_download_status(real_scrape_manager):
    mock_status = (TEST_ALBUM_URL, DownloadStatus.OK)
    real_scrape_manager.scrape_handler.album_tracker.log_download_status(*mock_status)

    status = real_scrape_manager.get_download_status
    assert status == {mock_status[0]: mock_status[1]}


def test_log_final_download_status(real_scrape_manager):
    url1, url2, url3 = TEST_ALBUM_URL + "1", TEST_ALBUM_URL + "2", TEST_ALBUM_URL + "3"
    mock_status = {
        url1: DownloadStatus.OK,
        url2: DownloadStatus.FAIL,
        url3: DownloadStatus.VIP,
    }
    for k, v in mock_status.items():
        real_scrape_manager.scrape_handler.album_tracker.log_download_status(k, v)
    real_scrape_manager.logger.info = MagicMock()
    real_scrape_manager.logger.error = MagicMock()
    real_scrape_manager.logger.warning = MagicMock()

    real_scrape_manager.log_final_download_status()

    real_scrape_manager.logger.info.assert_any_call("Download finished, showing download status")
    real_scrape_manager.logger.info.assert_any_call(f"{url1}: Download successful")
    real_scrape_manager.logger.error.assert_called_once_with(f"{url2}: Unexpected error")
    real_scrape_manager.logger.warning.assert_called_once_with(f"{url3}: VIP images found")


# ===================== Test ScrapeHandler =====================


def test_scrape_album_list(real_scrape_handler):
    test_url = "https://example.com/actor"
    # dry_run is False
    real_scrape_handler._real_scrape = MagicMock()
    real_scrape_handler.scrape_album_list(test_url, 1, False)
    real_scrape_handler._real_scrape.assert_called_once_with(test_url, 1, "album_list")

    test_url = "https://example.com/actor"
    # dry_run is True
    real_scrape_handler.scrape_album = MagicMock()
    real_scrape_handler.scrape_album_list(test_url, 1, True)
    real_scrape_handler.scrape_album.assert_not_called()


def test_scrape_album(real_scrape_handler, mock_logger):
    real_scrape_handler.album_tracker.is_downloaded = MagicMock(return_value=False)
    real_scrape_handler.album_tracker.log_downloaded = MagicMock(return_value=False)
    real_scrape_handler._real_scrape = MagicMock(
        return_value=[("http://example.com/image1.jpg", "image1")]
    )

    # dry_run is False
    real_scrape_handler.scrape_album(TEST_ALBUM_URL, 1, dry_run=False)
    real_scrape_handler._real_scrape.assert_called_once_with(TEST_ALBUM_URL, 1, "album_image")
    real_scrape_handler.album_tracker.log_downloaded.assert_called_once_with(TEST_ALBUM_URL)

    # dry_run is True
    real_scrape_handler.logger = mock_logger
    real_scrape_handler.album_tracker.log_downloaded.reset_mock()
    real_scrape_handler._real_scrape.reset_mock()
    real_scrape_handler.scrape_album(TEST_ALBUM_URL, 1, dry_run=True)

    real_scrape_handler._real_scrape.assert_called_once_with(TEST_ALBUM_URL, 1, "album_image")
    real_scrape_handler.album_tracker.log_downloaded.assert_not_called()
    real_scrape_handler.logger.info.assert_called()


def test_update_runtime_config(real_scrape_handler, real_runtime_config):
    wrong_runtime_config = MagicMock()
    with pytest.raises(TypeError):
        real_scrape_handler.update_runtime_config(wrong_runtime_config)

    correct_runtime_config = real_runtime_config(ServiceType.ASYNC, logging.ERROR)
    real_scrape_handler.update_runtime_config(correct_runtime_config)

    assert real_scrape_handler.runtime_config == correct_runtime_config


@patch("v2dl.utils.LinkParser.parse_input_url", return_value=(["album"], 1))
def test_get_scrape_type(mock_parse_input_url, real_scrape_handler):
    scrape_type = real_scrape_handler._get_scrape_type()
    assert scrape_type == "album_image"


@patch("v2dl.utils.LinkParser.add_page_num", return_value="http://example.com/album?page=1")
def test_scrape_single_page(mock_add_page_num, real_scrape_handler):
    mock_strategy = MagicMock()
    mock_strategy.is_vip_page = MagicMock(return_value=False)
    mock_strategy.get_xpath = MagicMock(return_value="//div")
    mock_strategy.process_page_links = MagicMock()

    results, should_continue = real_scrape_handler._scrape_single_page(
        TEST_ALBUM_URL, 1, mock_strategy, "album_list"
    )

    mock_add_page_num.assert_called_once()
    assert results == []
    assert not should_continue
