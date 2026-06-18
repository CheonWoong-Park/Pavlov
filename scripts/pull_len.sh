#!/bin/bash
# 길이 실험(31884) 생성물 견고 회수. 90초마다, 최대 ~10h, 인스턴스 down 시 재시도.
# ALL_DONE 감지 시 최종 pull 후 종료.
cp /mnt/d/DEV/pavlov/Gang_new2.pem /tmp/_klen.pem 2>/dev/null; chmod 600 /tmp/_klen.pem
K=/tmp/_klen.pem; H=root@betelgeuse.cloud.vessl.ai; PORT=31884
SSHO=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15)
DEST=/mnt/d/DEV/pavlov/eval_pull_len
mkdir -p "$DEST"
for i in $(seq 1 400); do            # 90s x 400 = 최대 ~10h
  ts=$(date '+%m-%d %H:%M:%S')
  if scp -i $K -P $PORT "${SSHO[@]}" -r "$H:/root/Pavlov/results/*" "$DEST/" >/dev/null 2>&1; then
    scp -i $K -P $PORT "${SSHO[@]}" "$H:/root/Pavlov/logs/len.out" "$DEST/len.out" >/dev/null 2>&1
    nd=$(wc -l < "$DEST/len_diff_gen.jsonl" 2>/dev/null || echo 0)
    na=$(wc -l < "$DEST/len_ar_gen.jsonl" 2>/dev/null || echo 0)
    echo "[$ts] OK  diff=$nd/240  ar=$na/240"
    if grep -q ALL_DONE "$DEST/len.out" 2>/dev/null; then
      scp -i $K -P $PORT "${SSHO[@]}" -r "$H:/root/Pavlov/results/*" "$DEST/" >/dev/null 2>&1
      echo "[$ts] >>> ALL_DONE. 최종 pull 완료, 종료 <<<"
      break
    fi
  else
    echo "[$ts] pull 실패(인스턴스 down/네트워크) - 재시도. 마지막 회수분은 $DEST 에 보존됨"
  fi
  sleep 90
done
echo "pull_len 종료 $(date '+%m-%d %H:%M:%S')"
