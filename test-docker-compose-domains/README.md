Bootstrap:

```bash
docker build owkin -t test-domain-owkin
docker build chunantes -t test-domain-chunantes
echo "127.0.0.1 hello.owkin.xyz hello.chunantes.xyz" | sudo tee -a /etc/hosts
```

Setup

```bash
docker-compose up -d
```

It works from the host:

```bash
$ curl hello.owkin.xyz:8042
Hello, I'm owkin!
```

It works from the container:

```bash
$ docker exec -it hello.owkin.xyz bash -c "curl hello.chunantes.xyz"
Hello, I'm chunantes!
```

Tear down:

```bash
docker rm -f $(docker ps -aq) 2>/dev/null
```
