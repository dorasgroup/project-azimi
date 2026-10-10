"""Compact model references resolved only against the exact frozen evidence set."""


def frozen_evidence_aliases(urls: list[str]) -> dict[str, str]:
    originals = set(urls)
    result = {}
    index = 1
    for url in dict.fromkeys(urls):
        alias = f"https://evidence.rasad.invalid/{index}"
        while alias in originals:
            index += 1
            alias = f"https://evidence.rasad.invalid/{index}"
        result[alias] = url
        index += 1
    return result
