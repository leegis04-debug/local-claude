# DS218 Hyper Backup 설정 (수동)

**목적**: 미니 PC 의 G 정본(`/volume1/docker/gstar-data`)을 DS218 에 시점별 보존.
Integrity Layer 의 외부 앵커 역할.

## 사전

- 미니 PC: SMB 공유 활성 (`/volume1/docker/gstar-data`)
- DS218: DSM 7.x, Hyper Backup 설치, 볼륨 여유 10GB+

## 백업 작업 생성

1. DSM → **Hyper Backup** → `+` → **폴더 및 패키지**
2. **목적지**: 로컬 폴더 & USB → `/volume1/backups/gstar/`
   (또는 Synology C2 원격 백업)
3. **소스**: `원격 서버` → `rsync (Synology)`
   - 호스트: `100.79.251.53` (미니 PC Meshnet IP)
   - 포트: 22
   - 사용자/비번 입력
   - 공유 폴더: `docker/gstar-data`
4. **스케줄**: 매일 03:00
5. **회전**: Smart Recycle (7 일 / 4 주 / 12 월 보존)
6. **압축·암호화**: 체크 (암호는 Keychain 에 별도 보관)

## 앵커 디렉토리 생성

백업 작업 최초 실행 후 DS218 에 다음 경로가 생성돼야 함:
```
/volume1/backups/gstar/<job_id>/<snapshot_ts>/
  docker/gstar-data/
    state/g.duckdb
    state/emb.faiss
```

`g verify anchor` 는 이 경로를 마운트한 맥북에서 호출한다. 맥북에서 SMB 마운트:
```
Finder → 서버 연결 → smb://ds218.local/backups/gstar
→ /Volumes/gstar-snapshots/ 로 마운트
```

그러면:
```bash
g verify anchor-write <cluster_id> --snapshot-dir /Volumes/gstar-snapshots
g verify anchor <cluster_id> --snapshot-dir /Volumes/gstar-snapshots
```

## 복원 (이직 후)

1. 회사 노트북 반납 전에 한 번 더 백업 실행 (최신 상태 확보)
2. 이직 후 맥북이나 새 노트북에:
   ```
   rsync -av /Volumes/gstar-snapshots/<latest>/docker/gstar-data/state/ \
             ~/.gstar/state/
   g verify chain --ns <old-namespace>    # 체인 유효성 확인
   ```
3. DS218 하나가 모든 이직 이력의 증거가 됨.
