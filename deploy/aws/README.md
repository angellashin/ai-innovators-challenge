# AWS EC2 배포 매뉴얼

현재 REPLAN MVP를 AWS Free Tier EC2 한 대에서 시연하기 위한 절차입니다.

```text
Nginx :80/:443
  └─ web :127.0.0.1:3000
       └─ /api/proxy → api :127.0.0.1:8000
worker ─┐
api ────┼─ replan_data 볼륨
web ────┘
```

현재는 대회 시연용입니다. SQLite와 업로드 파일은 EC2 Docker volume에 보관하며, 인스턴스 장애에 대비한 백업은 별도 설정이 필요합니다.

## 1. 계정과 비용 보호

1. Root 계정 MFA를 활성화합니다.
2. `Billing and Cost Management → Budgets`에서 월 예산과 50/80/100% 알림을 만듭니다.
3. 리전을 `Asia Pacific (Seoul) / ap-northeast-2`로 고정합니다.
4. Root 비밀번호, AWS Access Key, PEM 파일을 팀 채널에 공유하지 않습니다.

## 2. EC2 생성

`EC2 → Instances → Launch instance`에서 다음을 선택합니다.

| 항목 | 값 |
| --- | --- |
| Name | `replan-demo` |
| AMI | Ubuntu Server 24.04 LTS |
| Architecture | x86_64 |
| Instance type | `Free tier eligible`인 `t3.small` 우선, 없으면 `t3.micro` |
| Storage | 암호화된 gp3 20~30GB |
| Public IPv4 | 활성화 |
| Key pair | 새 키 생성 후 `.pem` 안전 보관 |

보안 그룹 인바운드는 `22/My IP`, `80/0.0.0.0/0`, `443/0.0.0.0/0`만 허용합니다. 3000과 8000은 외부에 열지 않습니다.

## 3. SSH와 기본 설치

로컬 터미널:

```sh
chmod 400 ~/Downloads/replan-demo.pem
ssh -i ~/Downloads/replan-demo.pem ubuntu@EC2_PUBLIC_IP
```

EC2 안에서:

```sh
git clone https://github.com/angellashin/ai-innovators-challenge.git
cd ai-innovators-challenge
bash deploy/aws/bootstrap-ubuntu.sh
```

설치 스크립트는 Docker·Compose plugin·Nginx·Git만 설치합니다. 끝나면 SSH를 끊고 다시 접속합니다.

## 4. 환경변수

```sh
cd ~/ai-innovators-challenge
cp .env.example .env
nano .env
```

서버의 `.env`에만 입력합니다.

```env
REPLAN_DEMO_TOKEN=긴_임의의_데모_토큰
REPLAN_CORS_ORIGINS=https://replan.example.com
REPLAN_ALLOWED_SOURCE_HOSTS=environment.ec.europa.eu
LLM_BASE_URL=https://gateway.example/v1
LLM_MODEL=bedrock-gpt-5.6-terra
API_KEY=발급받은_LLM_API_KEY
REPLAN_PAID_CALLS_ENABLED=false
REPLAN_MAX_PAID_RUNS_PER_DAY=20
```

`API_KEY`는 LLM 게이트웨이 키이고 `REPLAN_DEMO_TOKEN`은 REPLAN API 보호용 값입니다. 서로 다른 값을 사용합니다.

심사위원용 공개 시연은 `REPLAN_LLM_MODE=replay`로 띄웁니다. 녹화된 LLM 응답만 쓰므로 서버에 `API_KEY`가 필요 없고 비용이 0이며, 데모 가이드의 흐름이 항상 같은 결과로 재현됩니다. `record`·`live`는 녹화본 재생도 하루 한도(`REPLAN_MAX_PAID_RUNS_PER_DAY`)를 소모하므로 여러 사람이 쓰는 공개 주소에서는 한도를 먼저 정합니다. 데모 토큰은 `openssl rand -hex 32`로 서버에서 만들고 화면에 출력하지 않습니다.

## 5. Compose 실행

```sh
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 api worker web
curl http://127.0.0.1:8000/health
```

API는 `healthy`, web·worker는 `running` 상태여야 합니다. 세 컨테이너는 `unless-stopped`로 재시작되며 SQLite와 업로드 파일은 `replan_data` volume에 유지됩니다.

## 6. Nginx 연결

설정 예시는 `default_server`라 도메인 없이 `http://EC2_PUBLIC_IP`로도 열립니다. Ubuntu 기본 사이트가 먼저 응답하지 않도록 지웁니다.

```sh
sudo cp deploy/aws/nginx/replan.conf.example /etc/nginx/sites-available/replan
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -s /etc/nginx/sites-available/replan /etc/nginx/sites-enabled/replan
sudo nginx -t
sudo systemctl reload nginx
```

도메인을 연결하면 `server_name`을 실제 도메인으로 바꾸고 HTTPS를 설정합니다. Elastic IP가 없으면 인스턴스를 중지·시작할 때 공인 IP가 바뀝니다(재부팅은 유지). 제출한 주소를 지키려면 Elastic IP를 붙이거나 중지하지 않습니다.

## 7. GitHub main 자동 배포

GitHub Actions는 PR과 `main` push에서 backend·web·Compose를 검사합니다. EC2의 systemd timer는 5분마다 최신 `main`을 확인하고, **그 커밋의 main CI가 성공했을 때만** `git merge --ff-only`, `docker compose up -d --build`, API·웹 헬스 체크를 실행합니다. GitHub 러너 등록이나 SSH 키를 GitHub에 추가할 필요가 없습니다.

EC2에서 한 번 설치합니다. 프로젝트 경로는 `/home/ubuntu/ai-innovators-challenge`입니다.

```sh
cd ~/ai-innovators-challenge
git pull --ff-only origin main
sudo bash deploy/aws/install-auto-deploy.sh
```

첫 배포와 이후 팀원의 `main` 변경은 CI가 성공하면 자동 반영됩니다. 서비스는 서버 부팅 후에도 다시 시작되고, 실패한 배포는 다음 주기에 재시도합니다. 로컬 변경이 있거나 CI가 실패하면 배포하지 않습니다. `.env`와 `replan_data` Docker volume은 유지됩니다.

```sh
sudo systemctl status replan-auto-deploy.timer --no-pager
sudo journalctl -u replan-auto-deploy.service -n 80 --no-pager
```

GitHub 저장소 설정에 남은 기존 Self-hosted Runner나 `EC2_*` Secrets는 이 방식에서 사용하지 않습니다. 운영 반영을 확인하기 전에는 삭제하지 마세요.

## 8. 수동 업데이트와 복구

```sh
cd ~/ai-innovators-challenge
git pull --ff-only origin main
docker compose up -d --build
docker compose ps
docker compose logs --tail=200 api worker web
```

대회 전에는 `replan_data` volume과 `.env`를 별도 안전한 위치에 백업합니다. `docker compose down -v`는 SQLite와 업로드 파일을 삭제할 수 있으므로 사용하지 않습니다.

팀원끼리 공유할 정보는 AWS 리전, EC2 인스턴스 ID, Public IPv4 또는 도메인, 실행 중인 Git SHA뿐입니다. 기존 PEM private key, GitHub Actions 배포 개인키, AWS Access Key, `API_KEY`, `REPLAN_DEMO_TOKEN`은 공유하지 않습니다.

## 다음 확장

현재 배포는 EC2·EBS·Docker Compose·Nginx만 사용합니다. 다중 사용자 서비스로 전환할 때 PostgreSQL/RDS, S3 비공개 버킷, Cognito 인증, ECS worker를 별도 단위로 추가합니다.
