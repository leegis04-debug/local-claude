#!/usr/bin/env python3
"""
맞춤 HWPX Fill 스크립트
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
과제: 중소기업 스마트서비스 지원사업
프로젝트: meet_and_future
연도: 2026
템플릿: 2026_smart_service_meet_and_future_template.json
테이블: 33개 | 필드: 159개
해시: d0be6e5f6c1aa4a3
생성일: 2026-04-07T18:04:15
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

사용법:
  python3 2026_smart_service_meet_and_future_hwpx_fill.py \
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
    "기업명(상호)": {
        "table": 0,
        "row": 1,
        "col": 1
    },
    "대표자명": {
        "table": 0,
        "row": 1,
        "col": 5
    },
    "주      소": {
        "table": 1,
        "row": 1,
        "col": 1
    },
    "사업자등록번호": {
        "table": 0,
        "row": 17,
        "col": 5
    },
    "업 종 코 드": {
        "table": 0,
        "row": 3,
        "col": 1
    },
    "주요생산품": {
        "table": 0,
        "row": 3,
        "col": 5
    },
    "과 제 명": {
        "table": 0,
        "row": 4,
        "col": 1
    },
    "주도입 시스템": {
        "table": 0,
        "row": 4,
        "col": 5
    },
    "사업책임자": {
        "table": 0,
        "row": 5,
        "col": 1
    },
    "책임자 연락처": {
        "table": 0,
        "row": 5,
        "col": 5
    },
    "책임자 이메일": {
        "table": 0,
        "row": 6,
        "col": 5
    },
    "사 업 기 간": {
        "table": 0,
        "row": 7,
        "col": 1
    },
    "총사업비(원)": {
        "table": 0,
        "row": 8,
        "col": 1
    },
    "지원요청금액(원)": {
        "table": 0,
        "row": 8,
        "col": 5
    },
    "지 원 분 야": {
        "table": 0,
        "row": 9,
        "col": 1
    },
    "2023년": {
        "table": 1,
        "row": 5,
        "col": 6
    },
    "매출액 (원)": {
        "table": 0,
        "row": 11,
        "col": 3
    },
    "수출액 (원)": {
        "table": 0,
        "row": 12,
        "col": 3
    },
    "영업이익률 (%)": {
        "table": 0,
        "row": 13,
        "col": 3
    },
    "종업원수 (명)": {
        "table": 0,
        "row": 14,
        "col": 3
    },
    "사무실 전화": {
        "table": 0,
        "row": 15,
        "col": 5
    },
    "휴대전화": {
        "table": 0,
        "row": 21,
        "col": 3
    },
    "이메일": {
        "table": 0,
        "row": 21,
        "col": 5
    },
    "사무실전화": {
        "table": 0,
        "row": 18,
        "col": 5
    },
    "법인명(상호)": {
        "table": 1,
        "row": 0,
        "col": 1
    },
    "성명 / 직위": {
        "table": 0,
        "row": 20,
        "col": 5
    },
    "첨부서류(각 1부)": {
        "table": 0,
        "row": 23,
        "col": 2
    },
    "대표자": {
        "table": 1,
        "row": 0,
        "col": 5
    },
    "사업자번호": {
        "table": 1,
        "row": 2,
        "col": 1
    },
    "홈페이지": {
        "table": 1,
        "row": 2,
        "col": 5
    },
    "S/W사업자신고번호": {
        "table": 1,
        "row": 3,
        "col": 1
    },
    "신고일자": {
        "table": 1,
        "row": 3,
        "col": 5
    },
    "설립년월일": {
        "table": 1,
        "row": 4,
        "col": 1
    },
    "자 본 금": {
        "table": 1,
        "row": 4,
        "col": 5
    },
    "매출액(원)": {
        "table": 1,
        "row": 6,
        "col": 2
    },
    "영업이익률(%)": {
        "table": 1,
        "row": 7,
        "col": 2
    },
    "종업원수(명)": {
        "table": 1,
        "row": 8,
        "col": 2
    },
    "관련 전문 인력 보유현황": {
        "table": 1,
        "row": 9,
        "col": 1
    },
    "특허 등 관련 기술 보유 현황": {
        "table": 1,
        "row": 10,
        "col": 1
    },
    "금액(천원)": {
        "table": 2,
        "row": 0,
        "col": 3
    },
    "등급": {
        "table": 3,
        "row": 0,
        "col": 5
    },
    "비고": {
        "table": 4,
        "row": 0,
        "col": 4
    },
    "구분": {
        "table": 28,
        "row": 0,
        "col": 4
    },
    "정부지원금": {
        "table": 5,
        "row": 0,
        "col": 5
    },
    "현물(도입기업인건비)": {
        "table": 5,
        "row": 1,
        "col": 3
    },
    "신청사업비": {
        "table": 5,
        "row": 2,
        "col": 1
    },
    "금액": {
        "table": 6,
        "row": 0,
        "col": 3
    },
    "공급기업 인건비 세부내역 참조": {
        "table": 6,
        "row": 1,
        "col": 2
    },
    "인건비 x 비율(%)": {
        "table": 6,
        "row": 2,
        "col": 2
    },
    "성명(S/W기술자직무)": {
        "table": 7,
        "row": 0,
        "col": 1
    },
    "참여율": {
        "table": 7,
        "row": 1,
        "col": 3
    },
    "수량": {
        "table": 11,
        "row": 0,
        "col": 8
    },
    "위탁용역": {
        "table": 11,
        "row": 1,
        "col": 4
    },
    "식": {
        "table": 11,
        "row": 1,
        "col": 6
    },
    "투입률": {
        "table": 12,
        "row": 0,
        "col": 7
    },
    "구축 후": {
        "table": 13,
        "row": 0,
        "col": 3
    },
    "가중치": {
        "table": 15,
        "row": 0,
        "col": 6
    },
    "핵심성과지표": {
        "table": 16,
        "row": 0,
        "col": 1
    },
    "작성요령": {
        "table": 17,
        "row": 0,
        "col": 1
    },
    "기존활용": {
        "table": 22,
        "row": 1,
        "col": 10
    },
    "기능명": {
        "table": 24,
        "row": 0,
        "col": 2
    },
    "총괄 PMOOO 부장": {
        "table": 25,
        "row": 1,
        "col": 25
    },
    "조정위원회": {
        "table": 26,
        "row": 2,
        "col": 2
    },
    "기술자문위원회": {
        "table": 26,
        "row": 3,
        "col": 2
    },
    "·총괄 PM·솔루션 개발PM·코디네이터": {
        "table": 25,
        "row": 5,
        "col": 14
    },
    "구축 PMOOO 팀장": {
        "table": 25,
        "row": 7,
        "col": 25
    },
    "교육훈련 담당": {
        "table": 26,
        "row": 6,
        "col": 2
    },
    "인프라 구축": {
        "table": 25,
        "row": 13,
        "col": 9
    },
    "OOO관리": {
        "table": 25,
        "row": 13,
        "col": 23
    },
    "OOO운영": {
        "table": 25,
        "row": 13,
        "col": 34
    },
    "·도입기업 : OOO·공급기업 : OOO": {
        "table": 25,
        "row": 15,
        "col": 34
    },
    "구 분": {
        "table": 31,
        "row": 0,
        "col": 1
    },
    "총괄 PM": {
        "table": 26,
        "row": 1,
        "col": 2
    },
    "구축 PM": {
        "table": 26,
        "row": 4,
        "col": 2
    },
    "품질관리 담당": {
        "table": 26,
        "row": 5,
        "col": 2
    },
    "도입기업": {
        "table": 26,
        "row": 7,
        "col": 2
    },
    "공급기업": {
        "table": 26,
        "row": 8,
        "col": 2
    },
    "단가(원/D)": {
        "table": 27,
        "row": 0,
        "col": 18
    },
    "프로젝트 착수보고": {
        "table": 27,
        "row": 2,
        "col": 3
    },
    "홍길동(IT PM)": {
        "table": 27,
        "row": 2,
        "col": 16
    },
    "기본모듈 설치": {
        "table": 27,
        "row": 4,
        "col": 3
    },
    "요구사항 분석": {
        "table": 27,
        "row": 6,
        "col": 3
    },
    "김철수(업무분석가)": {
        "table": 27,
        "row": 6,
        "col": 16
    },
    "이영희(업무분석가)": {
        "table": 27,
        "row": 7,
        "col": 16
    },
    "Gap 분석": {
        "table": 27,
        "row": 8,
        "col": 3
    },
    "기준정보 수집 및 업무분석": {
        "table": 27,
        "row": 10,
        "col": 3
    },
    "인터페이스 정의": {
        "table": 27,
        "row": 12,
        "col": 3
    },
    "시스템 기능 설계": {
        "table": 27,
        "row": 14,
        "col": 3
    },
    "운영 시나리오 작성": {
        "table": 27,
        "row": 16,
        "col": 3
    },
    "인터페이스 개발": {
        "table": 27,
        "row": 18,
        "col": 3
    },
    "추가 및 변경기능 개발": {
        "table": 27,
        "row": 20,
        "col": 3
    },
    "단위테스트": {
        "table": 27,
        "row": 22,
        "col": 3
    },
    "통합테스트": {
        "table": 27,
        "row": 24,
        "col": 3
    },
    "기초데이터 이전": {
        "table": 27,
        "row": 26,
        "col": 3
    },
    "관리자교육": {
        "table": 27,
        "row": 28,
        "col": 3
    },
    "운영자교육": {
        "table": 27,
        "row": 30,
        "col": 3
    },
    "사용자교육": {
        "table": 27,
        "row": 32,
        "col": 3
    },
    "현업 시범적용": {
        "table": 27,
        "row": 34,
        "col": 3
    },
    "현장테스트 실시/검수": {
        "table": 27,
        "row": 36,
        "col": 3
    },
    "오픈 최종점검": {
        "table": 27,
        "row": 38,
        "col": 3
    },
    "인수인계 및 안정화": {
        "table": 27,
        "row": 40,
        "col": 3
    },
    "프로젝트 완료보고": {
        "table": 27,
        "row": 42,
        "col": 3
    },
    "선택": {
        "table": 28,
        "row": 20,
        "col": 4
    },
    "필수": {
        "table": 28,
        "row": 22,
        "col": 4
    },
    "교육시간": {
        "table": 29,
        "row": 0,
        "col": 2
    },
    "1H/회, 1회": {
        "table": 29,
        "row": 1,
        "col": 2
    },
    "2H/회, 1회": {
        "table": 29,
        "row": 3,
        "col": 2
    },
    "9H, 5일": {
        "table": 29,
        "row": 4,
        "col": 2
    },
    "정기점검": {
        "table": 31,
        "row": 1,
        "col": 1
    },
    "수시점검": {
        "table": 31,
        "row": 2,
        "col": 1
    },
    "성능점검 및 분석": {
        "table": 31,
        "row": 3,
        "col": 1
    },
    "관리계획서 제출": {
        "table": 31,
        "row": 4,
        "col": 1
    },
    "상태보고및 업무매뉴얼": {
        "table": 31,
        "row": 5,
        "col": 1
    },
    "사업관리": {
        "table": 31,
        "row": 6,
        "col": 1
    },
    "기타 지원": {
        "table": 31,
        "row": 7,
        "col": 1
    },
    "솔루션 및 장비명": {
        "table": 32,
        "row": 0,
        "col": 2
    },
    "기존장비": {
        "table": 32,
        "row": 1,
        "col": 4
    },
    "소프트웨어": {
        "table": 32,
        "row": 2,
        "col": 1
    },
    "하드웨어": {
        "table": 32,
        "row": 7,
        "col": 1
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
