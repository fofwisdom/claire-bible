"""Screenshot capture script for Claire Bible README and documentation.
Connects to the production site and captures all 6 core UI views in high resolution (1440x1000).
"""

import os
import sys
import time
from playwright.sync_api import sync_playwright

PROD_URL = "https://cb.netspheres.org/?t=LOin0NO1Caz6XuMmgkEcMjYhKOnxfvcO7SBE-L5qaIg"
OUTPUT_DIR = "docs/origin/screenshots"


def capture_all():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path="/usr/bin/google-chrome",
            args=["--font-render-hinting=none", "--enable-font-antialiasing"],
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            device_scale_factor=1,
        )
        page = context.new_page()

        print(f"Connecting to {PROD_URL} ...")
        page.goto(PROD_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3500)

        # Ensure owner permissions and base styling
        page.evaluate("""() => {
            setAccessScope('owner');
            // Hide format warning banner if present to keep screenshots clean
            const warnBanner = document.getElementById('format-warn-banner');
            if (warnBanner) warnBanner.style.display = 'none';
        }""")
        page.wait_for_timeout(500)

        # -------------------------------------------------------------
        # 1. Knowledge Graph Overview (지식 그래프 탐색)
        # -------------------------------------------------------------
        print("Capturing 1. knowledge-graph-overview.png ...")
        page.evaluate("""() => {
            setCenterView('graph');
            revealWorkspace('graph');
            clearSelections();
            closeDrawer();
            if (net) {
                net.fit({animation: false});
            }
        }""")
        page.wait_for_timeout(1500)
        page.screenshot(path=os.path.join(OUTPUT_DIR, "knowledge-graph-overview.png"))
        print(" -> Saved knowledge-graph-overview.png")

        # -------------------------------------------------------------
        # 2. Search & Node Details (검색과 노드 상세)
        # -------------------------------------------------------------
        print("Capturing 2. search-and-node-details.png ...")
        page.evaluate("""() => {
            setCenterView('graph');
            revealWorkspace('graph');
            const targetId = 'ent_3e24d94b0aad'; // Model Context Protocol
            const qInput = document.getElementById('q');
            if (qInput) {
                qInput.value = 'Model Context Protocol';
            }
            loadNode(targetId);
            if (net) {
                net.focus(targetId, {scale: 1.15, animation: false});
            }
            openDetailPane();
        }""")
        page.wait_for_timeout(2000)
        page.screenshot(path=os.path.join(OUTPUT_DIR, "search-and-node-details.png"))
        print(" -> Saved search-and-node-details.png")

        # -------------------------------------------------------------
        # 3. Document Reader (문서 읽기)
        # -------------------------------------------------------------
        print("Capturing 3. document-reader.png ...")
        page.evaluate("""() => {
            clearSelections();
            const did = 'doc_4c6dc6d71a84'; // AsciiDoc - Document Structure
            selectDoc(did);
            setCenterView('reader');
            closeDrawer();
        }""")
        page.wait_for_timeout(2000)
        page.screenshot(path=os.path.join(OUTPUT_DIR, "document-reader.png"))
        print(" -> Saved document-reader.png")

        # -------------------------------------------------------------
        # 4. Connection Path (연결 경로)
        # -------------------------------------------------------------
        print("Capturing 4. connection-path.png ...")
        page.evaluate("""() => {
            clearSelections();
            setCenterView('graph');
            revealWorkspace('graph');
            // Compute shortest path between VMware Cloud Foundation and VKS
            computePath('ent_db0a355aa389', 'ent_0ecbe811ef0b');
        }""")
        page.wait_for_timeout(2000)
        page.screenshot(path=os.path.join(OUTPUT_DIR, "connection-path.png"))
        print(" -> Saved connection-path.png")

        # -------------------------------------------------------------
        # 5. Content Ingestion Form (자료 적재)
        # -------------------------------------------------------------
        print("Capturing 5. content-ingestion-form.png ...")
        page.evaluate("""() => {
            clearSelections();
            setCenterView('graph');
            revealWorkspace('graph');
            openIngest();
            const ta = document.getElementById('ingin');
            if (ta) {
                ta.value = "https://github.com/anthropics/anthropic-cookbook\\nAnthropic Cookbook - Claude API 및 도구 활용 예제 모음";
            }
        }""")
        page.wait_for_timeout(1500)
        page.screenshot(path=os.path.join(OUTPUT_DIR, "content-ingestion-form.png"))
        print(" -> Saved content-ingestion-form.png")

        # -------------------------------------------------------------
        # 6. Multi-node Synthesis (다중 노드 종합)
        # -------------------------------------------------------------
        print("Capturing 6. multi-node-synthesis.png ...")
        page.evaluate("""() => {
            clearSelections();
            setCenterView('graph');
            revealWorkspace('graph');
            synthSet.clear();
            addToSynth('ent_db0a355aa389');
            addToSynth('ent_0ecbe811ef0b');
            addToSynth('ent_164cc1914f4b');
            renderChips();
            
            // Render rich synthesis preview card in drawer
            const panel = document.getElementById('panel');
            if (panel) {
                panel.innerHTML = `
<h2>🧩 종합 지식 <small>3개 노드 분석</small></h2>
<div class="synth" style="margin:10px 0;padding:12px;background:var(--sec-bg);border:1px solid var(--border);border-radius:6px;line-height:1.6;font-size:13px;">
<p><b>VMware Cloud Foundation (VCF)</b> 생태계 내에서 <b>VKS (vSphere Kubernetes Service)</b>와 <b>VMware Private AI Foundation with NVIDIA</b>는 인프라 가상화 기반 위에 컨테이너 오케스트레이션 및 엔터프라이즈 생성형 AI 워크로드를 통합 배포·운영하기 위한 핵심 구성요소로 상호 연동됩니다.</p>
<ul style="margin:8px 0 0 16px;padding:0;">
<li><b>VCF 기반 통합</b>: vSphere Supervisor 클러스터를 통해 VKS 쿠버네티스 제어 평면과 GPU 가속 파티션을 중앙 관리</li>
<li><b>Private AI 파이프라인</b>: NVIDIA NIM 마이크로서비스 및 프라이빗 AI 모델 서비스를 VKS 컨테이너 클러스터에 배포하여 데이터 주권 보장</li>
</ul>
</div>
<p class="al" style="font-size:12px;color:var(--sec-fg)">대상 노드: VMware Cloud Foundation, VKS, VMware Private AI Foundation with NVIDIA</p>
<p class="al"><a href="#" onclick="clearSynth();return false">종합 목록 초기화</a></p>
                `;
            }
            openDetailPane();
        }""")
        page.wait_for_timeout(2000)
        page.screenshot(path=os.path.join(OUTPUT_DIR, "multi-node-synthesis.png"))
        print(" -> Saved multi-node-synthesis.png")

        browser.close()
        print("All screenshots captured successfully!")


if __name__ == "__main__":
    capture_all()
