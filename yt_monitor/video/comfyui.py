"""로컬 AI 이미지 생성 (ComfyUI) — 무료, 그래픽카드(RTX 등)에서 실행.

스톡 영상보다 강렬한 장면(예: "a giant shark under a neon city at night")이 필요할 때,
씬마다 Ollama가 쓴 이미지 묘사로 세로 이미지를 만들고, 합성 단계에서 천천히 줌을 줘서 영상처럼 쓴다.

설치 (한 번만):
  1) https://www.comfy.org/download 에서 ComfyUI Desktop(Windows, NVIDIA) 설치 → 실행
  2) SDXL 계열 모델(.safetensors)을 받아 ComfyUI의 models/checkpoints 폴더에 넣기
     추천: Juggernaut XL Lightning 등 "Lightning/Turbo" 모델 → 6스텝이면 RTX 5060에서 몇 초
     (모델마다 이용 조건이 다르니 상업적 이용 가능 여부를 받는 페이지에서 확인하세요)
  3) ComfyUI를 켜 둔 채로 프로그램 설정에서 "AI 이미지 생성" 체크

API (ComfyUI 기본 주소 http://127.0.0.1:8188):
  GET  /system_stats                          서버 확인
  GET  /object_info/CheckpointLoaderSimple    설치된 모델 목록
  POST /prompt                                워크플로 실행 → prompt_id
  GET  /history/<prompt_id>                   완료 여부 + 결과 이미지 이름
  GET  /view?filename=…                       이미지 받기
  POST /free                                  그래픽카드 메모리 비우기

단독 테스트:
    python -m yt_monitor.video.comfyui --check
    python -m yt_monitor.video.comfyui "a giant shark under a neon city at night" --out samples/ai
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import requests

log = logging.getLogger(__name__)

DEFAULT_HOST = "http://127.0.0.1:8188"
DOWNLOAD_URL = "https://www.comfy.org/download"

SETUP_HELP = f"""ComfyUI(로컬 AI 이미지 생성)에 연결할 수 없습니다.

1. {DOWNLOAD_URL} 에서 ComfyUI Desktop(Windows · NVIDIA)을 설치하고 실행하세요.
2. SDXL 계열 모델(.safetensors, 예: Juggernaut XL Lightning)을 받아
   ComfyUI 의 models/checkpoints 폴더에 넣으세요.
3. ComfyUI 를 켜 둔 채로 다시 시도하세요. (주소: 설정의 ai_images.host, 기본 {DEFAULT_HOST})

AI 이미지를 쓰지 않으려면 설정에서 "AI 이미지 생성"을 끄면 됩니다."""


class ComfyError(RuntimeError):
    pass


@dataclass
class ComfyStatus:
    server_up: bool
    checkpoints: list[str] = field(default_factory=list)
    checkpoint: str = ""
    host: str = DEFAULT_HOST

    @property
    def ok(self) -> bool:
        return self.server_up and bool(self.checkpoint)

    @property
    def message(self) -> str:
        if not self.server_up:
            return SETUP_HELP
        if not self.checkpoints:
            return ("ComfyUI는 켜져 있지만 models/checkpoints 폴더에 모델이 없습니다.\n"
                    "SDXL 계열 모델(.safetensors)을 넣고 ComfyUI를 다시 시작하세요.")
        if not self.checkpoint:
            return (f"설정한 모델을 찾지 못했습니다. 설치된 모델: {', '.join(self.checkpoints)}\n"
                    "설정의 ai_images.checkpoint 를 비우면 첫 번째 모델을 씁니다.")
        return f"ComfyUI 준비 완료 ({self.checkpoint})"


def build_workflow(prompt: str, negative: str, *, checkpoint: str, width: int, height: int, steps: int,
                   cfg: float, sampler: str, scheduler: str, seed: int, prefix: str = "ytmonitor") -> dict:
    """ComfyUI 기본 txt2img 워크플로 (API 형식)."""
    return {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["4", 1]}},
        "3": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": sampler, "scheduler": scheduler,
            "denoise": 1.0, "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
            "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["8", 0]}},
    }


def tuned_params(checkpoint: str, steps: int, cfg: float, sampler: str, scheduler: str) -> tuple[int, float, str, str]:
    """모델 이름에 맞는 권장값. ByteDance SDXL-Lightning은 steps/cfg/sampler가 틀리면 흐릿하게 나온다.

    sdxl_lightning_4step → 4스텝 · cfg 1 · euler · sgm_uniform (2step/8step도 이름대로)
    RealVisXL / Juggernaut XL Lightning (실사 모델) → 5스텝 · cfg 1.5 · dpmpp_sde · karras (배포 페이지 권장값)
    그 밖의 모델은 설정값을 그대로 쓴다.
    """
    name = checkpoint.lower()
    if re.search(r"sdxl[_-]?lightning", name):
        m = re.search(r"(\d+)\s*-?step", name)
        return (int(m.group(1)) if m else 4), 1.0, "euler", "sgm_uniform"
    if "lightning" in name and any(k in name for k in REALISTIC_MODELS):
        return 5, 1.5, "dpmpp_sde", "karras"
    return steps, cfg, sampler, scheduler


# 모델을 따로 지정하지 않았을 때 먼저 고르는 실사(photorealistic) 모델 이름 조각 (앞일수록 우선)
REALISTIC_MODELS = ("realvis", "juggernaut", "epicrealism", "realistic")


def pick_checkpoint(checkpoints: list[str]) -> str:
    """여러 모델이 있으면 실사 모델을 우선 (RealVisXL > Juggernaut > …), 없으면 첫 번째."""
    for key in REALISTIC_MODELS:
        for c in checkpoints:
            if key in c.lower():
                return c
    return checkpoints[0] if checkpoints else ""


class ComfyClient:
    def __init__(self, host: str = DEFAULT_HOST, *, checkpoint: str = "", width: int = 768, height: int = 1344,
                 steps: int = 6, cfg: float = 2.0, sampler: str = "dpmpp_sde", scheduler: str = "karras",
                 style: str = "", negative: str = "", timeout: float = 300, auto_tune: bool = True,
                 session: requests.Session | None = None, poll_interval: float = 0.5):
        self.host = host.rstrip("/")
        self.checkpoint = checkpoint
        self.width, self.height = int(width), int(height)
        self.steps, self.cfg = int(steps), float(cfg)
        self.sampler, self.scheduler = sampler, scheduler
        self.style, self.negative = style, negative
        self.timeout = timeout
        self.auto_tune = auto_tune
        self.poll_interval = poll_interval
        self.session = session or requests.Session()
        self.session.trust_env = False      # 로컬 서버: 시스템 프록시를 타지 않게
        self.client_id = uuid.uuid4().hex

    @classmethod
    def from_config(cls, ai_cfg: dict) -> "ComfyClient":
        return cls(ai_cfg.get("host") or DEFAULT_HOST, checkpoint=ai_cfg.get("checkpoint") or "",
                   width=ai_cfg.get("width", 768), height=ai_cfg.get("height", 1344),
                   steps=ai_cfg.get("steps", 6), cfg=ai_cfg.get("cfg", 2.0),
                   sampler=ai_cfg.get("sampler", "dpmpp_sde"), scheduler=ai_cfg.get("scheduler", "karras"),
                   style=ai_cfg.get("style", ""), negative=ai_cfg.get("negative", ""),
                   timeout=float(ai_cfg.get("timeout_sec", 300)), auto_tune=bool(ai_cfg.get("auto_tune", True)))

    # ---- 상태 -------------------------------------------------------------------
    def list_checkpoints(self) -> list[str]:
        resp = self.session.get(f"{self.host}/object_info/CheckpointLoaderSimple", timeout=10)
        resp.raise_for_status()
        spec = resp.json()["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"]
        # 예전 형식: [[이름...], {...}] / 새 형식: ["COMBO", {"options": [이름...]}]
        if spec and isinstance(spec[0], list):
            return list(spec[0])
        if len(spec) > 1 and isinstance(spec[1], dict):
            return list(spec[1].get("options") or [])
        return []

    def status(self) -> ComfyStatus:
        try:
            self.session.get(f"{self.host}/system_stats", timeout=5).raise_for_status()
        except requests.RequestException:
            return ComfyStatus(False, host=self.host)
        try:
            ckpts = self.list_checkpoints()
        except (requests.RequestException, KeyError, ValueError, IndexError):
            ckpts = []
        chosen = self.checkpoint if self.checkpoint in ckpts else ("" if self.checkpoint else pick_checkpoint(ckpts))
        return ComfyStatus(True, ckpts, chosen, self.host)

    # ---- 생성 -------------------------------------------------------------------
    def generate(self, prompt: str, out_path: Path, *, seed: int | None = None,
                 cancel: threading.Event | None = None) -> Path:
        """prompt → out_path(png). 실패하면 ComfyError."""
        st = self.status()
        if not st.ok:
            raise ComfyError(st.message)
        full = f"{prompt}, {self.style}" if self.style else prompt
        steps, cfg, sampler, scheduler = (tuned_params(st.checkpoint, self.steps, self.cfg, self.sampler, self.scheduler)
                                          if self.auto_tune else (self.steps, self.cfg, self.sampler, self.scheduler))
        wf = build_workflow(full, self.negative, checkpoint=st.checkpoint, width=self.width, height=self.height,
                            steps=steps, cfg=cfg, sampler=sampler, scheduler=scheduler,
                            seed=seed if seed is not None else random.randint(0, 2**31 - 1))
        try:
            resp = self.session.post(f"{self.host}/prompt", json={"prompt": wf, "client_id": self.client_id},
                                     timeout=30)
        except requests.RequestException as exc:
            raise ComfyError(f"ComfyUI 요청 실패: {exc}") from exc
        if resp.status_code >= 400:
            raise ComfyError(f"ComfyUI가 워크플로를 거부했습니다 ({resp.status_code}): {resp.text[:400]}")
        prompt_id = resp.json()["prompt_id"]

        started = time.monotonic()
        while True:
            if cancel is not None and cancel.is_set():
                self._interrupt()
                raise InterruptedError
            if time.monotonic() - started > self.timeout:
                self._interrupt()
                raise ComfyError(f"ComfyUI 이미지 생성 시간 초과 ({self.timeout:.0f}초)")
            hist = self.session.get(f"{self.host}/history/{prompt_id}", timeout=10).json().get(prompt_id)
            if hist:
                status = hist.get("status") or {}
                if status.get("status_str") == "error":
                    msgs = [m for m in status.get("messages", []) if m and m[0] == "execution_error"]
                    detail = msgs[0][1].get("exception_message", "") if msgs else ""
                    raise ComfyError(f"ComfyUI 이미지 생성 오류: {detail or status}")
                images = [img for out in (hist.get("outputs") or {}).values() for img in out.get("images", [])]
                if images:
                    return self._download(images[0], Path(out_path))
                if status.get("completed"):
                    raise ComfyError("ComfyUI가 이미지를 만들지 않았습니다.")
            time.sleep(self.poll_interval)

    def _download(self, img: dict, out_path: Path) -> Path:
        resp = self.session.get(f"{self.host}/view", params={
            "filename": img["filename"], "subfolder": img.get("subfolder", ""), "type": img.get("type", "output")},
            timeout=60)
        resp.raise_for_status()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(resp.content)
        return out_path

    def _interrupt(self):
        try:
            self.session.post(f"{self.host}/interrupt", timeout=5)
        except requests.RequestException:
            pass

    def free_memory(self):
        """다 만든 뒤 그래픽카드 메모리를 비운다 (다른 프로그램/Ollama가 쓸 수 있게)."""
        try:
            self.session.post(f"{self.host}/free", json={"unload_models": True, "free_memory": True}, timeout=10)
        except requests.RequestException:
            pass


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="ComfyUI 이미지 생성 단독 테스트")
    parser.add_argument("prompt", nargs="?")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--checkpoint", default="")
    parser.add_argument("--steps", type=int, default=6)
    parser.add_argument("--cfg", type=float, default=2.0)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--out", default="samples/ai")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    client = ComfyClient(args.host, checkpoint=args.checkpoint, steps=args.steps, cfg=args.cfg)
    st = client.status()
    print(st.message)
    if st.checkpoints:
        print("설치된 모델: " + ", ".join(st.checkpoints))
    if not st.ok or args.check or not args.prompt:
        return 0 if st.ok else 1
    t = time.monotonic()
    path = client.generate(args.prompt, Path(args.out) / "ai_test.png")
    print(f"✔ {path} ({time.monotonic() - t:.1f}초)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
