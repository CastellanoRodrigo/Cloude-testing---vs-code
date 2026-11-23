"""Pruebas de la validación de URL con PDF e imágenes. Solo datos falsos."""

import pytest

from data_guardian import fetcher
from data_guardian.fetcher import FetchError, parse_url


@pytest.mark.parametrize("name", ["a.txt", "a.csv", "a.pdf", "a.PNG", "a.jpg", "a.jpeg"])
def test_accepted_extensions(name):
    assert parse_url(f"https://ejemplo.com/{name}")[3] == name


@pytest.mark.parametrize("name", ["a.docx", "a.exe", "a"])
def test_rejected_extensions(name):
    with pytest.raises(FetchError, match=r"\.pdf, \.png o \.jpg"):
        parse_url(f"https://ejemplo.com/{name}")


def test_limit_is_10_mb():
    assert fetcher.MAX_BYTES == 10 * 1024 * 1024
