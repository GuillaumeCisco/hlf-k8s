### Bootstrap

```
./build-images.sh
./add-hosts.sh
```

## Test with docker-compose

Setup

```
docker-compose up -d
```

It works from the host:

```
$ curl hello.owkin.xyz:8042
Hello, I'm owkin!
```

It works from the container:

```
$ docker exec -it hello.owkin.xyz bash -c "curl hello.chunantes.xyz"
Hello, I'm chunantes!
```

Tear down

```
docker rm -f $(docker ps -aq) 2>/dev/null
```
