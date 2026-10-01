# Hands-on DevOps Tasks Roadmap

A structured, phased practice roadmap to build, containerize, and deploy this multi-tier application from scratch.

**How to use this guide**:
- Follow the milestones below in order.
- Research and implement each task independently to build real-world DevOps muscle memory.
- If you get stuck or want to verify your approach, refer to the configuration files and manifests in this repository for guidance and reference solutions.

## Tasks

### Phase 1: Basic Containerization
- [ ] Create single-stage Dockerfiles for the FastAPI backend, worker, and Nginx frontend using standard base images.
- [ ] Build your images locally and verify they run and serve traffic.

### Phase 2: Multi-Stage Build & Distroless Hardening
- [ ] Write optimized multi-stage Dockerfiles for the backend and worker using Google Distroless runtime images.
- [ ] Build an optimized Nginx container for the frontend static assets.
- [ ] Tag and push your container images to your Docker Hub repository.

### Phase 3: Multi-Container Setup with Docker Compose
- [ ] Write a `docker-compose.yml` file defining all services: PostgreSQL, Redis, Backend, Worker, and Frontend.
- [ ] Configure custom bridge networking for service discovery and named persistent volumes for database storage.
- [ ] Verify multi-container communication, voting flow, and data persistence across container restarts.

### Phase 4: Kubernetes Deployment on Minikube
- [ ] Create Kubernetes Secret manifests for database credentials.
- [ ] Create ConfigMap manifests for backend settings, Nginx configuration, and database initialization scripts.
- [ ] Deploy PostgreSQL and Redis using StatefulSets with persistent volume claims (PVC).
- [ ] Deploy the Backend, Worker, and Frontend using Deployment manifests with resource limits and health probes.
- [ ] Expose services internally using ClusterIP and configure Ingress for external routing on Minikube.

### Phase 5: Kustomize Environment Overlays
- [ ] Structure your Kubernetes manifests into a reusable `base` directory.
- [ ] Create a `dev` overlay with single-replica deployments, lightweight resources, and a dev ingress host.
- [ ] Create a `prod` overlay with multi-replica scaling, production resource limits, and a prod ingress host.
- [ ] Verify and deploy environments independently using `kubectl apply -k`.
