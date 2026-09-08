"""One bounded CLI invocation at a time; no pipeline logic lives in the web UI."""
import atexit
import os
import subprocess
import sys
import threading
import time

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

_lock = threading.Lock()
_process = None
_state = {"status": "idle"}


def _stream_fd(pipe, is_err: bool, acc: list):
    try:
        for line in iter(pipe.readline, ""):
            acc.append(line)
            target = sys.stderr if is_err else sys.stdout
            prefix = "\033[33m[openoutreach err]\033[0m " if is_err else "\033[36m[openoutreach]\033[0m "
            target.write(f"{prefix}{line}")
            target.flush()
    except Exception:
        pass
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def _wait(process):
    import io
    if not isinstance(getattr(process, "stdout", None), (io.IOBase, io.BufferedReader, io.TextIOBase)):
        try:
            stdout, stderr = process.communicate(timeout=900)
            code = getattr(process, "returncode", 0)
            with _lock:
                if _process is process and _state["status"] == "running":
                    err_msg = (stderr or "").strip()
                    if len(err_msg) > 1000:
                        err_msg = err_msg[-1000:]
                    _state.update(
                        status="completed" if code == 0 else "failed",
                        exit_code=code,
                        error=err_msg if code != 0 else "",
                    )
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            with _lock:
                if _process is process:
                    _state.update(status="timeout", error="A busca atingiu o limite de 15 minutos.")
        return

    sys.stdout.write("\n\033[32m════════════════════════════════════════════════════════════\033[0m\n")
    sys.stdout.write("\033[32m[openoutreach] Iniciando busca e qualificação de leads...\033[0m\n")
    sys.stdout.write("\033[32m════════════════════════════════════════════════════════════\033[0m\n")
    sys.stdout.flush()

    out_lines: list[str] = []
    err_lines: list[str] = []

    t_out = threading.Thread(target=_stream_fd, args=(process.stdout, False, out_lines), daemon=True)
    t_err = threading.Thread(target=_stream_fd, args=(process.stderr, True, err_lines), daemon=True)
    t_out.start()
    t_err.start()

    try:
        code = process.wait(timeout=900)
        t_out.join(timeout=2)
        t_err.join(timeout=2)
        with _lock:
            if _process is process and _state["status"] == "running":
                err_msg = "".join(err_lines).strip()
                if len(err_msg) > 1000:
                    err_msg = err_msg[-1000:]
                status_str = "completed" if code == 0 else "failed"
                _state.update(
                    status=status_str,
                    exit_code=code,
                    error=err_msg if code != 0 else "",
                )
                if code == 0:
                    sys.stdout.write("\n\033[32m[openoutreach] ✓ Busca de leads concluída com sucesso!\033[0m\n")
                    sys.stdout.write("\033[32m[openoutreach] Enriquecendo leads captados com WhatsApp...\033[0m\n")
                    sys.stdout.flush()
                    try:
                        from openoutreach.whatsapp import enrich_all_leads
                        enrich_all_leads(use_web_search=True)
                        sys.stdout.write("\033[32m[openoutreach] ✓ Enriquecimento com WhatsApp finalizado.\033[0m\n\n")
                    except Exception as wex:
                        sys.stdout.write(f"\033[33m[openoutreach] Enriquecimento WhatsApp avisou: {wex}\033[0m\n\n")
                    sys.stdout.flush()
                else:
                    sys.stderr.write(f"\n\033[31m[openoutreach] ✗ Busca finalizada com código {code}.\033[0m\n\n")
                sys.stdout.flush()
                sys.stderr.flush()
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        with _lock:
            if _process is process:
                _state.update(status="timeout", error="A busca atingiu o limite de 15 minutos.")
                sys.stderr.write("\033[31m[openoutreach] A busca atingiu o limite de 15 minutos e foi cancelada.\033[0m\n")
                sys.stderr.flush()


@atexit.register
def _shutdown():
    if _process is not None and _process.poll() is None:
        _process.terminate()


@require_http_methods(["GET", "POST"])
def job(request):
    global _process
    with _lock:
        if request.method == "GET":
            return JsonResponse(_state.copy())
        if request.POST.get("action") == "cancel":
            if _process is not None and _process.poll() is None:
                _process.terminate()
                _state.update(status="cancelled")
            return JsonResponse(_state.copy())
        if _process is not None and _process.poll() is None:
            return JsonResponse({"error": "Já existe uma busca em andamento."}, status=409)
        try:
            count = int(request.POST.get("count", ""))
            if not 1 <= count <= 100:
                raise ValueError
        except ValueError:
            return JsonResponse({"error": "Escolha entre 1 e 100 leads."}, status=400)
        env = os.environ.copy()
        env["DJANGO_SETTINGS_MODULE"] = "openoutreach.settings"
        env["OPENOUTREACH_DB"] = str(settings.DATABASE_PATH)
        env["PYTHONUNBUFFERED"] = "1"
        args = [sys.executable, "-m", "openoutreach", "find", str(count)]
        try:
            _process = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except OSError:
            return JsonResponse({"error": "Não foi possível iniciar a busca."}, status=500)
        _state.clear()
        _state.update(status="running", count=count, started_at=time.time())
        threading.Thread(target=_wait, args=(_process,), daemon=True).start()
        return JsonResponse(_state.copy(), status=202)
