"""Offline vocabulary and evidence checks shared by capture and analytics.

This layer only keeps values supported by a specific equipment mention or by
the same previously sanitized row. Model knowledge is never evidence.
"""
from __future__ import annotations

import copy
from functools import lru_cache
import json
import math
import re
import unicodedata
from pathlib import Path


def fold(value):
    return "".join(c for c in unicodedata.normalize("NFD", str(value or "").casefold())
                   if unicodedata.category(c) != "Mn").strip()


def clean(value):
    if not isinstance(value, str):
        return None
    value = value.strip().strip(" .,!?:;")
    return None if fold(value) in {"", "unknown", "desconocido", "null", "none", "n/a"} else value[:200]


MODALITY_ALIASES = [
    ("pet/ct", "PET/CT"), ("pet ct", "PET/CT"),
    ("resonancia magnetica", "MR"), ("magnetic resonance", "MR"),
    *[(a, "MR") for a in ("resonancias", "resonancia", "resonadores", "resonador", "mri", "mr", "rm")],
    *[(a, "CT") for a in ("tomografia computarizada", "computed tomography", "tomografos", "tomografo", "tomografia", "scanner ct", "cat", "cts", "ct")],
    *[(a, "Ultrasound") for a in ("ultrasounds", "ultrasound", "ultrasonidos", "ultrasonido", "ecografias", "ecografia", "ecografos", "ecografo")],
    *[(a, "X-Ray") for a in ("rayos x", "x-ray", "xray", "radiografia", "radiografias")],
    *[(a, "Mammography") for a in ("mammography", "mamografia", "mamografo")],
    ("c-arm", "C-Arm"), ("arco en c", "C-Arm"),
    ("fluoroscopy", "Fluoroscopy"), ("fluoroscopia", "Fluoroscopy"),
    *[(a, "Patient Monitor") for a in ("patient monitor", "monitor de paciente", "monitoring", "monitores", "monitor")],
    ("ventilator", "Ventilator"), ("ventilador", "Ventilator"),
    ("ecg", "ECG"), ("electrocardiografo", "ECG"),
    ("defibrillator", "Defibrillator"), ("desfibrilador", "Defibrillator"),
    ("spect", "SPECT"), ("pet", "PET"),
]
MODALITY_MAP = {fold(a): c for a, c in MODALITY_ALIASES}


def normalize_modality(value):
    return MODALITY_MAP.get(fold(value), clean(value))


NUMBER_WORDS = dict(zip(
    "cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciseis diecisiete dieciocho diecinueve veinte".split(),
    range(21)))
NUMBER_WORDS.update(dict(zip("zero one two three four five six seven eight nine ten eleven twelve".split(), range(13))))
NUMBER_WORDS.update({"un": 1, "una": 1, "a": 1, "an": 1})
NUMBER_PATTERN = r"\d+(?:[.,]\d+)?|" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))


def number(value):
    token = fold(value)
    try:
        n = float(NUMBER_WORDS[token] if token in NUMBER_WORDS else token.replace(",", "."))
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def explicit_age(text):
    t = fold(text)
    # Ranges and installation years need a richer schema; never invent a midpoint.
    if re.search(rf"(?:{NUMBER_PATTERN})\s*(?:-|–|a|to)\s*(?:{NUMBER_PATTERN})\s*(?:anos|years?)", t):
        return None
    m = re.search(rf"(?<![\w.-])({NUMBER_PATTERN})\s*(?:anos|years?)\b", t)
    age = number(m[1]) if m else None
    return age if age is not None and 0 <= age <= 100 else None


def mentioned(value, text):
    return bool(clean(value) and re.search(rf"(?<!\w){re.escape(fold(value))}(?!\w)", fold(text)))


def equipment_mentions(text):
    t = fold(text)
    hits = []
    for alias, canonical in sorted(MODALITY_ALIASES, key=lambda x: -len(x[0])):
        for match in re.finditer(rf"\b{re.escape(alias)}\b", t):
            if any(match.start() < h["end"] and match.end() > h["start"] for h in hits):
                continue
            prefix = t[max(0, match.start()-55):match.start()]
            count = re.search(rf"\b({NUMBER_PATTERN})\s*(?:(?:equipos?|sistemas?|unidades?|maquinas?)\s+(?:de\s+)?)?$", prefix)
            quantity = number(count[1]) if count else None
            if quantity is not None and (quantity < 1 or quantity > 10000 or not quantity.is_integer()):
                quantity = None
            hits.append({"modality": canonical, "start": match.start(), "end": match.end(),
                         "quantity": int(quantity) if quantity is not None else None})
    hits.sort(key=lambda h: h["start"])
    for i, hit in enumerate(hits):
        end = hits[i+1]["start"] if i+1 < len(hits) else len(t)
        hit["text"] = t[hit["start"]:end]
    return hits


@lru_cache(maxsize=1)
def country_catalog():
    return json.loads((Path(__file__).parent / "geography.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def country_aliases():
    catalog = country_catalog()
    aliases = {fold(k): v for k, v in catalog["aliases"].items()}
    aliases.update({fold(c): c for rows in catalog["regions"].values() for c in rows})
    return aliases


def normalize_country(value):
    return country_aliases().get(fold(value), clean(value))


def ground_extraction(data, source_text=None, context=None):
    data = copy.deepcopy(data) if isinstance(data, dict) else {}
    context = context or {}
    text = source_text or ""
    result = {"customer_name": "Desconocido", "city": None, "country": None, "equipment": []}
    aliases = country_aliases()
    for field in ("customer_name", "city", "country"):
        value = clean(data.get(field))
        old = clean(context.get(field))
        supported = mentioned(value, text)
        if field == "country" and value:
            supported = any(mentioned(alias, text) for alias, canonical in aliases.items()
                            if canonical == normalize_country(value))
        if supported:
            result[field] = normalize_country(value) if field == "country" else value
        elif old:
            result[field] = old

    if mentioned("Ciudad de Panamá", text):
        result["city"] = "Ciudad de Panamá"
    hits = equipment_mentions(text)
    previous = context.get("equipment") or []
    consumed = set()
    used_previous = set()
    raw_equipment = data.get("equipment")
    if not isinstance(raw_equipment, list):
        raw_equipment = []
    for index, raw in enumerate(raw_equipment[:50]):
        if not isinstance(raw, dict):
            continue
        modality = normalize_modality(raw.get("modality"))
        available = [(i, h) for i, h in enumerate(hits) if i not in consumed and h["modality"] == modality]
        # Correct the known small-model CT/X-Ray confusion using explicit text.
        if not available and len(hits) == 1 and len(raw_equipment) == 1:
            available = [(0, hits[0])]
            modality = hits[0]["modality"]
        available.sort(key=lambda pair: -sum(mentioned(raw.get(f), pair[1]["text"])
                                             for f in ("manufacturer", "model", "serial_number")))
        hit = available[0][1] if available else None
        if available:
            consumed.add(available[0][0])
        old_candidates = [(i, eq) for i, eq in enumerate(previous) if i not in used_previous
                          and eq.get("modality") == modality
                          and all(not clean(raw.get(f)) or not clean(eq.get(f)) or fold(raw[f]) == fold(eq[f])
                                  for f in ("manufacturer", "model", "serial_number"))]
        old_pair = old_candidates[0] if len(old_candidates) == 1 else next((p for p in old_candidates if p[0] == index), None)
        old = old_pair[1] if old_pair else {}
        if old_pair:
            used_previous.add(old_pair[0])
        if not hit and not old:
            continue
        segment = hit["text"] if hit else ""
        eq = {"modality": modality}
        for field in ("manufacturer", "model", "serial_number"):
            value = clean(raw.get(field))
            eq[field] = value if mentioned(value, segment) else clean(old.get(field))
        if fold(eq.get("model")) == fold(eq.get("manufacturer")):
            eq["model"] = None
        eq["quantity"] = hit["quantity"] if hit and hit["quantity"] is not None else old.get("quantity")
        age = explicit_age(segment)
        eq["estimated_age"] = age if age is not None else old.get("estimated_age")
        estimated = re.search(r"\b(aproximadamente|aprox|unos|unas|parece|around|about|approximately|estimated)\b", segment)
        eq["state"] = "Estimado" if estimated else old.get("state", "Reportado")
        # Only the field actually asked about may consume an unqualified reply.
        key = str(context.get("_question_key") or "").split(".")
        if not hits and old_pair and len(key) == 3 and key[:2] == ["equipment", str(old_pair[0])]:
            field = key[2]
            if field in ("manufacturer", "model", "serial_number"):
                value = clean(raw.get(field))
                if mentioned(value, text):
                    eq[field] = value
            if field == "estimated_age":
                eq[field] = explicit_age(text)
                if eq[field] is not None and re.search(r"\b(aproximadamente|unos|unas|around|about)\b", fold(text)):
                    eq["state"] = "Estimado"
        if fold(eq.get("model")) == fold(eq.get("manufacturer")):
            eq["model"] = None
        result["equipment"].append(eq)
    # A model omitting a prior row cannot silently delete confirmed context.
    result["equipment"].extend(copy.deepcopy(eq) for i, eq in enumerate(previous) if i not in used_previous)
    return result
