#!/usr/bin/env python3
"""
맞춤 HWPX Fill 스크립트
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
과제: 농식품AI상용화
프로젝트: agri-food-ai
연도: 2026
템플릿: 2026_agri-ai_agri-food-ai_template.json
테이블: 86개 | 필드: 260개
해시: 3ff178e8b238dde1
생성일: 2026-04-08T17:06:29
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

사용법:
  python3 2026_agri-ai_agri-food-ai_hwpx_fill.py \
    <원본양식.hwpx> <데이터.json> <출력.hwpx>

데이터 JSON 형식:
  {
    "fields": {
      "기업명(상호)": "주식회사 OOO",
      "대표자명": "홍길동"
    },
    "direct": {
      "ㅇㅇㅇ@ㅇㅇㅇ.ㅇㅇ": "real@email.com"
    }
  }
"""

import json
import os
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

# ── 필드 맵 (라벨 → 셀 위치) ── 이 템플릿에 최적화됨 ──

FIELD_MAP = {
    "해당": {
        "table": 2,
        "row": 0,
        "col": 3
    },
    "□ 생산·공정혁신": {
        "table": 5,
        "row": 1,
        "col": 18
    },
    "□ 유통·시장": {
        "table": 5,
        "row": 2,
        "col": 19
    },
    "□ 품질관리": {
        "table": 5,
        "row": 3,
        "col": 18
    },
    "□ 소비·식품": {
        "table": 5,
        "row": 4,
        "col": 18
    },
    "□ 자연어처리(NLP)": {
        "table": 5,
        "row": 5,
        "col": 16
    },
    "□ 지식표현·추론": {
        "table": 5,
        "row": 6,
        "col": 16
    },
    "AI인프라": {
        "table": 5,
        "row": 7,
        "col": 2
    },
    "상용화대상 명칭": {
        "table": 15,
        "row": 0,
        "col": 1
    },
    "↳ 간단 설명": {
        "table": 5,
        "row": 9,
        "col": 2
    },
    "신청(지원) 유형": {
        "table": 5,
        "row": 10,
        "col": 2
    },
    "기술수준": {
        "table": 5,
        "row": 11,
        "col": 2
    },
    "상용화 준비 수준(중복 표기 가능)": {
        "table": 5,
        "row": 12,
        "col": 2
    },
    "컨소시엄 구성": {
        "table": 5,
        "row": 13,
        "col": 2
    },
    "이름": {
        "table": 85,
        "row": 1,
        "col": 3
    },
    "참여기업1": {
        "table": 5,
        "row": 16,
        "col": 3
    },
    "사업책임자": {
        "table": 5,
        "row": 17,
        "col": 14
    },
    "참여기업2": {
        "table": 5,
        "row": 17,
        "col": 3
    },
    "1. 기업정보": {
        "table": 10,
        "row": 0,
        "col": 1
    },
    "주관기업명": {
        "table": 14,
        "row": 1,
        "col": 1
    },
    "설 립 일": {
        "table": 14,
        "row": 1,
        "col": 3
    },
    "대 표 자": {
        "table": 14,
        "row": 2,
        "col": 1
    },
    "회계담당자": {
        "table": 10,
        "row": 4,
        "col": 7
    },
    "사업자등록번호": {
        "table": 14,
        "row": 2,
        "col": 3
    },
    "업    종": {
        "table": 14,
        "row": 3,
        "col": 1
    },
    "주생산품(제품/서비스)": {
        "table": 10,
        "row": 6,
        "col": 1
    },
    "매 출 액": {
        "table": 10,
        "row": 7,
        "col": 1
    },
    "고용인원(4대보험가입자)": {
        "table": 10,
        "row": 7,
        "col": 7
    },
    "투자금": {
        "table": 10,
        "row": 10,
        "col": 6
    },
    "~’24년": {
        "table": 10,
        "row": 11,
        "col": 2
    },
    "백만원": {
        "table": 10,
        "row": 11,
        "col": 6
    },
    "’25년": {
        "table": 10,
        "row": 12,
        "col": 2
    },
    "사무실": {
        "table": 10,
        "row": 13,
        "col": 2
    },
    "연구소": {
        "table": 10,
        "row": 14,
        "col": 2
    },
    "공장": {
        "table": 10,
        "row": 15,
        "col": 2
    },
    "기업소개(요약)": {
        "table": 10,
        "row": 16,
        "col": 1
    },
    "2. 재무현황": {
        "table": 11,
        "row": 0,
        "col": 1
    },
    "2024년": {
        "table": 11,
        "row": 1,
        "col": 3
    },
    "총자산": {
        "table": 11,
        "row": 2,
        "col": 1
    },
    "총부채": {
        "table": 11,
        "row": 3,
        "col": 1
    },
    "자기자본": {
        "table": 11,
        "row": 4,
        "col": 1
    },
    "매출액": {
        "table": 11,
        "row": 5,
        "col": 1
    },
    "영업이익": {
        "table": 11,
        "row": 6,
        "col": 1
    },
    "당기순이익": {
        "table": 11,
        "row": 7,
        "col": 1
    },
    "지식재산권 종류": {
        "table": 12,
        "row": 1,
        "col": 7
    },
    "등록번호": {
        "table": 12,
        "row": 1,
        "col": 13
    },
    "특허(등록·출원), 실용신안 등": {
        "table": 12,
        "row": 2,
        "col": 7
    },
    "사업명": {
        "table": 72,
        "row": 13,
        "col": 2
    },
    "지원금액(원)": {
        "table": 12,
        "row": 8,
        "col": 13
    },
    "참여기업명": {
        "table": 43,
        "row": 0,
        "col": 1
    },
    "□ 대학": {
        "table": 10,
        "row": 2,
        "col": 10
    },
    "참여기업 역할": {
        "table": 10,
        "row": 3,
        "col": 1
    },
    "법인등록번호": {
        "table": 14,
        "row": 3,
        "col": 3
    },
    "신청유형": {
        "table": 16,
        "row": 0,
        "col": 1
    },
    "컨소시엄 구성 여부": {
        "table": 16,
        "row": 1,
        "col": 1
    },
    "↳ 컨소시엄 참여기업": {
        "table": 16,
        "row": 2,
        "col": 2
    },
    "합  계(100%)": {
        "table": 17,
        "row": 0,
        "col": 12
    },
    "(현물)자기부담금의 90% 이하": {
        "table": 17,
        "row": 1,
        "col": 11
    },
    "상용화 건수(제품 및 서비스)": {
        "table": 52,
        "row": 0,
        "col": 5
    },
    "국민체감활동*": {
        "table": 52,
        "row": 1,
        "col": 5
    },
    "투자": {
        "table": 52,
        "row": 2,
        "col": 5
    },
    "국내": {
        "table": 52,
        "row": 3,
        "col": 5
    },
    "수출": {
        "table": 52,
        "row": 4,
        "col": 5
    },
    "정성": {
        "table": 52,
        "row": 5,
        "col": 3
    },
    "상용화 대상명": {
        "table": 18,
        "row": 1,
        "col": 2
    },
    "설명": {
        "table": 18,
        "row": 2,
        "col": 2
    },
    "이미지": {
        "table": 18,
        "row": 3,
        "col": 2
    },
    "상용화 배경 및 필요성": {
        "table": 18,
        "row": 5,
        "col": 2
    },
    "주관기업-참여기업협업 추진배경 및 역할": {
        "table": 18,
        "row": 6,
        "col": 2
    },
    "추진전략": {
        "table": 18,
        "row": 7,
        "col": 2
    },
    "목표시장 및 주요 고객층": {
        "table": 18,
        "row": 8,
        "col": 2
    },
    "실현가능성": {
        "table": 18,
        "row": 9,
        "col": 2
    },
    "국민체감형 확산계획 및 효과": {
        "table": 18,
        "row": 10,
        "col": 2
    },
    "구입 가격": {
        "table": 36,
        "row": 0,
        "col": 6
    },
    "추진 기간": {
        "table": 38,
        "row": 0,
        "col": 2
    },
    "성별": {
        "table": 41,
        "row": 0,
        "col": 4
    },
    "채용년월": {
        "table": 41,
        "row": 0,
        "col": 10
    },
    "최종 학위": {
        "table": 41,
        "row": 1,
        "col": 7
    },
    "주요 경력": {
        "table": 41,
        "row": 9,
        "col": 3
    },
    "00제약": {
        "table": 43,
        "row": 1,
        "col": 1
    },
    "판매 아이템": {
        "table": 46,
        "row": 0,
        "col": 3
    },
    "○○마트": {
        "table": 46,
        "row": 1,
        "col": 1
    },
    "수출품목수": {
        "table": 47,
        "row": 0,
        "col": 3
    },
    "○○개": {
        "table": 47,
        "row": 1,
        "col": 3
    },
    "국제인증 건수": {
        "table": 48,
        "row": 0,
        "col": 2
    },
    "○○건": {
        "table": 48,
        "row": 1,
        "col": 2
    },
    "추진 계획": {
        "table": 57,
        "row": 0,
        "col": 2
    },
    "계획수립 및 자료조사": {
        "table": 57,
        "row": 2,
        "col": 2
    },
    "시설/장비 구축": {
        "table": 57,
        "row": 3,
        "col": 2
    },
    "시제품 실증": {
        "table": 57,
        "row": 4,
        "col": 2
    },
    "외주가공, 디자인개발": {
        "table": 57,
        "row": 5,
        "col": 2
    },
    "양산형 제품 완성": {
        "table": 57,
        "row": 6,
        "col": 2
    },
    "중간보고서 제출": {
        "table": 57,
        "row": 7,
        "col": 2
    },
    "판로 확보": {
        "table": 57,
        "row": 8,
        "col": 2
    },
    "홍보 활동 및 성과 창출": {
        "table": 57,
        "row": 9,
        "col": 2
    },
    "최종보고서 제출": {
        "table": 57,
        "row": 10,
        "col": 2
    },
    "국고지원금": {
        "table": 66,
        "row": 3,
        "col": 4
    },
    "현금": {
        "table": 61,
        "row": 1,
        "col": 3
    },
    "20억원(지원금 합계의 70%)": {
        "table": 61,
        "row": 2,
        "col": 2
    },
    "0.857144억원(자기부담금의 00%)": {
        "table": 61,
        "row": 3,
        "col": 3
    },
    "자기부담금액": {
        "table": 62,
        "row": 1,
        "col": 5
    },
    "재료비": {
        "table": 75,
        "row": 9,
        "col": 1
    },
    "인건비": {
        "table": 75,
        "row": 1,
        "col": 1
    },
    "소계": {
        "table": 63,
        "row": 11,
        "col": 3
    },
    "시설장비비": {
        "table": 75,
        "row": 7,
        "col": 1
    },
    "합계": {
        "table": 64,
        "row": 15,
        "col": 2
    },
    "금액": {
        "table": 63,
        "row": 1,
        "col": 4
    },
    "구분": {
        "table": 78,
        "row": 0,
        "col": 2
    },
    "자기부담금": {
        "table": 64,
        "row": 11,
        "col": 3
    },
    "용역비": {
        "table": 75,
        "row": 5,
        "col": 1
    },
    "회계정산비": {
        "table": 75,
        "row": 15,
        "col": 1
    },
    "국고/자부담(현금)": {
        "table": 64,
        "row": 13,
        "col": 4
    },
    "참여율(%)(C)": {
        "table": 66,
        "row": 0,
        "col": 10
    },
    "현물": {
        "table": 66,
        "row": 1,
        "col": 4
    },
    "대표": {
        "table": 66,
        "row": 1,
        "col": 7
    },
    "12개월": {
        "table": 66,
        "row": 1,
        "col": 9
    },
    "’26.7.예정": {
        "table": 66,
        "row": 5,
        "col": 5
    },
    "대리": {
        "table": 66,
        "row": 5,
        "col": 7
    },
    "8개월": {
        "table": 66,
        "row": 5,
        "col": 9
    },
    "참여형태": {
        "table": 71,
        "row": 0,
        "col": 1
    },
    "비목구분": {
        "table": 69,
        "row": 1,
        "col": 1
    },
    "건명": {
        "table": 69,
        "row": 2,
        "col": 1
    },
    "집행금액": {
        "table": 69,
        "row": 3,
        "col": 1
    },
    "수행 기간": {
        "table": 69,
        "row": 3,
        "col": 4
    },
    "계약대상": {
        "table": 69,
        "row": 4,
        "col": 1
    },
    "계약대상과 이해관계 여부*": {
        "table": 69,
        "row": 4,
        "col": 4
    },
    "목표(적)": {
        "table": 69,
        "row": 5,
        "col": 1
    },
    "상세내용": {
        "table": 69,
        "row": 6,
        "col": 1
    },
    "결과 활용계획 및 방안": {
        "table": 69,
        "row": 7,
        "col": 1
    },
    "기타 사항": {
        "table": 69,
        "row": 8,
        "col": 1
    },
    "□ 사업 총책임자": {
        "table": 71,
        "row": 1,
        "col": 6
    },
    "□ 자유": {
        "table": 71,
        "row": 2,
        "col": 7
    },
    "상용화 대상 명칭": {
        "table": 71,
        "row": 3,
        "col": 1
    },
    "학교명": {
        "table": 72,
        "row": 0,
        "col": 4
    },
    "00학사": {
        "table": 72,
        "row": 1,
        "col": 3
    },
    "00석사": {
        "table": 72,
        "row": 2,
        "col": 3
    },
    "00박사": {
        "table": 72,
        "row": 3,
        "col": 3
    },
    "직위(급)": {
        "table": 72,
        "row": 4,
        "col": 4
    },
    "연 도": {
        "table": 72,
        "row": 9,
        "col": 2
    },
    "지원금집행 비목": {
        "table": 75,
        "row": 0,
        "col": 1
    },
    "지급수수료": {
        "table": 75,
        "row": 3,
        "col": 1
    },
    "여비": {
        "table": 75,
        "row": 11,
        "col": 1
    },
    "광고선전비": {
        "table": 75,
        "row": 13,
        "col": 1
    },
    "공통 사항": {
        "table": 75,
        "row": 17,
        "col": 1
    },
    "금액(원)": {
        "table": 76,
        "row": 0,
        "col": 3
    },
    "특별": {
        "table": 76,
        "row": 1,
        "col": 1
    },
    "1시간": {
        "table": 76,
        "row": 4,
        "col": 2
    },
    "초과(매시간)": {
        "table": 76,
        "row": 5,
        "col": 2
    },
    "A4 25행1매당": {
        "table": 76,
        "row": 6,
        "col": 2
    },
    "60,000 이내": {
        "table": 76,
        "row": 7,
        "col": 3
    },
    "35,000 이내": {
        "table": 76,
        "row": 8,
        "col": 3
    },
    "1인당": {
        "table": 76,
        "row": 9,
        "col": 2
    },
    "100,000/시간": {
        "table": 76,
        "row": 10,
        "col": 3
    },
    "다급": {
        "table": 77,
        "row": 1,
        "col": 4
    },
    "가급": {
        "table": 78,
        "row": 1,
        "col": 1
    },
    "아시아오세아니아": {
        "table": 78,
        "row": 10,
        "col": 2
    },
    "아메리카": {
        "table": 78,
        "row": 11,
        "col": 2
    },
    "유럽": {
        "table": 78,
        "row": 12,
        "col": 2
    },
    "중동아프리카": {
        "table": 78,
        "row": 13,
        "col": 2
    },
    "정보제공자": {
        "table": 85,
        "row": 0,
        "col": 3
    }
}

# ── 네임스페이스 ──

NS_P = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"

_NS_REGISTRY = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
    "hh": "http://www.hancom.co.kr/hwpml/2011/head",
    "hc": "http://www.hancom.co.kr/hwpml/2011/core",
    "ha": "http://www.hancom.co.kr/hwpml/2011/app",
    "hm": "http://www.hancom.co.kr/hwpml/2011/master-page",
    "hpf": "http://www.hancom.co.kr/schema/2011/hpf",
    "opf": "http://www.idpf.org/2007/opf/",
    "odf": "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0",
    "ocf": "urn:oasis:names:tc:opendocument:xmlns:container",
    "config": "urn:oasis:names:tc:opendocument:xmlns:config:1.0",
    "hp10": "http://www.hancom.co.kr/hwpml/2016/paragraph",
    "ooxmlchart": "http://www.hancom.co.kr/hwpml/2016/ooxmlchart",
    "hwpunitchar": "http://www.hancom.co.kr/hwpml/2016/HwpUnitChar",
}
for _p, _u in _NS_REGISTRY.items():
    ET.register_namespace(_p, _u)


# ── 핵심 함수 ──

def find_cell(tbl, row_addr, col_addr):
    for tr in tbl.findall(f"{NS_P}tr"):
        for tc in tr.findall(f"{NS_P}tc"):
            addr = tc.find(f"{NS_P}cellAddr")
            if addr is not None:
                if int(addr.get("rowAddr", "-1")) == row_addr and int(addr.get("colAddr", "-1")) == col_addr:
                    return tc
    return None


def set_cell_text(tc, text):
    first_t = None
    for t in tc.iter(f"{NS_P}t"):
        if first_t is None:
            first_t = t
            first_t.text = text
        else:
            t.text = ""
    if first_t is None:
        for run in tc.iter(f"{NS_P}run"):
            t_elem = ET.SubElement(run, f"{NS_P}t")
            t_elem.text = text
            break


def fill(source_hwpx, data, output_path):
    """HWPX를 데이터로 채우기"""
    work_dir = tempfile.mkdtemp(prefix="hwpx_cf_")
    try:
        with zipfile.ZipFile(source_hwpx) as zf:
            zf.extractall(work_dir)

        contents_dir = os.path.join(work_dir, "Contents")
        section_files = sorted(
            f for f in os.listdir(contents_dir)
            if re.match(r"section\d+\.xml", f)
        )

        filled = 0
        for sf in section_files:
            path = os.path.join(contents_dir, sf)
            tree = ET.parse(path)
            root = tree.getroot()
            tables = list(root.iter(f"{NS_P}tbl"))

            # Label 기반
            fields = data.get("fields", {}) if "fields" in data else data
            for label, value in fields.items():
                if label in ("fields", "direct"):
                    continue
                if label not in FIELD_MAP:
                    continue
                info = FIELD_MAP[label]
                if info["table"] < len(tables):
                    tc = find_cell(tables[info["table"]], info["row"], info["col"])
                    if tc is not None:
                        set_cell_text(tc, str(value))
                        filled += 1

            # Direct 교체
            for old, new in data.get("direct", {}).items():
                for t in root.iter(f"{NS_P}t"):
                    if t.text and old in t.text:
                        t.text = t.text.replace(old, new)
                        filled += 1

            tree.write(path, encoding="UTF-8", xml_declaration=True)

        # 재압축
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            mt = os.path.join(work_dir, "mimetype")
            if os.path.exists(mt):
                zf.write(mt, "mimetype", compress_type=zipfile.ZIP_STORED)
            for dp, _, fns in os.walk(work_dir):
                for fn in sorted(fns):
                    if fn == "mimetype" and dp == work_dir:
                        continue
                    full = os.path.join(dp, fn)
                    zf.write(full, os.path.relpath(full, work_dir))

        print(f"완료: {filled}건 채움 → {output_path}")
        return output_path
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def load_data(path):
    """JSON 데이터 로드. {"fields": {}, "direct": {}} 또는 플랫 {"라벨": "값"}"""
    with open(path, "r", encoding="utf-8") as f:
        return json.loads(f.read())


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    source = sys.argv[1]
    data_path = sys.argv[2]
    output = sys.argv[3]
    data = load_data(data_path)
    fill(source, data, output)
