#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  Ethereal Engine — Startup Script
#  Usage: ./scripts/start.sh
# ═══════════════════════════════════════════════════════════════
set -e

BLUE='\033[0;34m' GREEN='\033[0;32m' YELLOW='\033[1;33m' RED='\033[0;31m' NC='\033[0m'

echo -e "${BLUE}"
echo "  ███████╗████████╗██╗  ██╗███████╗██████╗ ███████╗ █████╗ ██╗"
echo "  ██╔════╝╚══██╔══╝██║  ██║██╔════╝██╔══██╗██╔════╝██╔══██╗██║"
echo "  █████╗     ██║   ███████║█████╗  ██████╔╝█████╗  ███████║██║"
echo "  ██╔══╝     ██║   ██╔══██║██╔══╝  ██╔══██╗██╔══╝  ██╔══██║██║"
echo "  ███████╗   ██║   ██║  ██║███████╗██║  ██║███████╗██║  ██║███████╗"
echo "  ╚══════╝   ╚═╝   ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚══════╝"
echo -e "${NC}"
echo -e "${GREEN}  RAG Pipeline v2.4 — Full Stack Startup${NC}"
echo ""

# Check Docker
if ! command -v docker &> /dev/null; then
  echo -e "${RED}✗ Docker not found. Install Docker Desktop: https://docker.com${NC}"; exit 1
fi
echo -e "${GREEN}✓ Docker found${NC}"

# Check Docker Compose
if ! docker compose version &> /dev/null; then
  echo -e "${RED}✗ Docker Compose not found${NC}"; exit 1
fi
echo -e "${GREEN}✓ Docker Compose found${NC}"

# Copy .env if missing
if [ ! -f .env ]; then
  cp .env.example .env
  echo -e "${YELLOW}⚠ Created .env from template — review and edit if needed${NC}"
fi

echo ""
echo -e "${BLUE}Starting services...${NC}"
docker compose up --build -d

echo ""
echo -e "${YELLOW}Waiting for Ollama to start...${NC}"
sleep 5

# Pull models if not present
echo -e "${BLUE}Ensuring Ollama models are ready...${NC}"
docker exec ethereal-ollama ollama pull llama3.2 2>/dev/null || true
docker exec ethereal-ollama ollama pull nomic-embed-text 2>/dev/null || true

echo ""
echo -e "${GREEN}════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ Ethereal Engine is running!${NC}"
echo -e "${GREEN}════════════════════════════════════════════${NC}"
echo ""
echo -e "  🌐 Frontend:  ${BLUE}http://localhost:3000${NC}"
echo -e "  📡 API:       ${BLUE}http://localhost:8000/api${NC}"
echo -e "  📚 API Docs:  ${BLUE}http://localhost:8000/docs${NC}"
echo -e "  🤖 Ollama:    ${BLUE}http://localhost:11434${NC}"
echo ""
echo -e "  ${YELLOW}Stop:${NC} docker compose down"
echo -e "  ${YELLOW}Logs:${NC} docker compose logs -f backend"
echo ""
