# Protocolo PC Link "remote" (titulo no visor, transporte e volume)

Recuperado por analise estatica (sem executar nada) dos arquivos do CD do
MCM595:

- `PhilipsRemote.exe` 8.00.0119, dentro de
  `MUSICMATCH/MMSETUP8119AENU.EXE` -> `Philips-USB-PDA_8.00.0119_Support_Files.mmz`.
  E o componente da Philips que o MusicMatch Jukebox usava para falar com o
  aparelho.
- `uacb.dll`, dentro de `TREIBER/INSTALL215.EXE` (instalador NSIS).
- `mc500cpl.exe` (Sound Agent 2), para comparacao.

Implementacao: `pc_link_protocol.py` (protocolo), `media_session.py` (musica
atual do Windows) e `philips_pc_link_core.py` (canal USB, volume).

## Transporte

- Aparelho -> PC: relatorio da interface de controle `80 <comando>`.
  Parametros, quando existem, sao lidos do mailbox HID: primeiro byte em
  `A4EE`, demais em `A4C1` (igual a `UacbGetHidBuffer`). O primeiro byte deve
  repetir o comando (ou ser 0); senao o PhilipsRemote descarta o pedido.
- PC -> aparelho: quadro `[bytes restantes, comando, 0x82 ok / 0x81 falha,
  dados...]`, no maximo 0x28 bytes.
- O PhilipsRemote grava do jeito antigo (`A4F4` = quadro, `A4F2` = 0,
  `B0A0` = `C0`, espera `A4F2 >= tamanho`). Quando o `uacb.dll` e carregado
  por `PhilipsRemote.exe` (ele confere o nome do processo), converte essas
  gravacoes em `UacbSendData`: `A4F4` = quadro, `A544` = primeiro byte do
  quadro, e espera `A544 = 00`. Para `0471:0111` esse e o transporte real, o
  mesmo do Sound Agent. O caminho `A4F2`/`B0A0` so e usado para `PID_0110`.
- O mailbox `A544` e criado pelo bloco de setup do `uacbflt.sys`
  (88 bytes em `A556` + ganchos em `A0DE/A0DD/A087/A086`), que o driver
  original gravava em toda unidade `Rev_0402`, FWM ou MCM. O app grava esse
  bloco na ativacao MCM e, no modo FWM, so no primeiro uso, se `A0DE` ainda
  nao apontar para `A556`.

## Comandos do aparelho

| Cod. | Nome original | Resposta do app |
|---|---|---|
| 01 02 0A | PLAY / STOP / PAUSE | comando no player atual do Windows |
| 03 04 | NEXT / PREVIOUS_TRACK | idem |
| 05 | MUTE_AUDIO | mudo (alvo de volume escolhido) |
| 06 07 | VOLUME_UP / DOWN | tecla de volume do Windows ou saida Philips |
| 08 | SET_PC_VOLUME | mailbox `[08, modo, valor]`; modo 1 ignorado; volume = valor*100/255 |
| 09 | SET_PC_BALANCE | consumido |
| 10 11 | SHUFFLE / REPEAT | alterna no player, se suportado |
| 12 13 | SEARCH_FORWARD / BACKWARD | avancar / voltar no player |
| 14-17, 1B, 1D-21, 25, 26, 30, 31, 70 | playlists, cursor, modelo etc. | consumidos sem resposta |
| 18 19 1A 1C | TRACK_NR / RECORD / LAUNCH_PLAYER_2 | `02 cc 81` |
| 22 | SET_UAC_VOLUME_CONTROL | mailbox `[22, p]`; grava `A4F3 = 1` se p > 0, senao `0` |
| 23 | SET_PC_EVENTS | mailbox `[23, p]`; bit 0 liga/desliga os avisos do PC |
| 40 | nome da faixa | **campo mostrado no visor**: janela atual de `Titulo - Artista` (ver Visor) |
| 41 42 | artista / album | `[n+2, cc, 82, texto ASCII <= 37]` ou `02 cc 81` |
| 43 44 45 46 | nomes CDDB / playlist | `02 cc 81` |
| 47 | CURRENT_PLAYTIME | `05 47 82 HH MM SS` (BCD) ou `02 47 81` |
| 48 | CURRENT_TRACK_NR | `03 48 81 00` (fixo no original) |
| 49 | DRIVER_READY | sem resposta |
| 4A | PLAYER1_STATUS | `02 4A 82` (player "ok") |
| 4B | PLAYER2_STATUS | `02 4B 83` |
| 4C | VOLUME_STATUS | `04 4C 82 00 vv`, vv = volume%*255/100 (0 se mudo) |
| 4D | PLAYER_VERSION | `06 4D 82 08 00 01 19` (MusicMatch 8.00.0119, BCD) |
| 4E | PLAYER_NAME | texto `Windows` |
| 4F | MODULE_SOFTWARE_VERSION | 4 bytes lidos em `0x1C4B` -> `06 4F 82 b0 b1 b2 b3` |
| 50 | GET_DATA | 40 bytes: `29 50 82 "random-data"` + zeros (identico ao original) |
| 51 | PLAYER1_STATE | mailbox `[51, e]` -> `02 e v`: ultimo valor enviado no aviso `e` (A1-A8); para A2 o estado atual |
| 52 | CURSOR_TEXT | mesma janela do visor (o FWM589 pede ao encontrar o PC) |

`60`-`6F` sao do Sound Agent (`docs/MCM_SOUND_AGENT_PROTOCOL.md`).
`80 80` e um relatorio autonomo, sem resposta.

## Avisos do PC (eventos A0-AA)

Bloqueados enquanto `SET_PC_EVENTS` estiver com o bit 0 em 0. Por padrao
ficam ligados (o original liga ao conectar).

- `02 A1 mm`: o que mudou. **Confirmado no FWM589:** `02 A1 02` faz o
  aparelho pedir `40` de novo e mostrar a resposta; os bits `01`, `04`, `08`,
  `10`, `20` e `80` nao geram pedido nesse modelo.
- `02 A2 ss`: estado do player: `00` parado, `01` tocando, `04` pausado,
  `05` terminado.
- `02 A9 40` / `02 A9 41`: erro de playlist / de faixa (nao usado).

O app so envia avisos depois que o aparelho fez algum pedido PC Link, para
nao escrever em modelos que nao usam esse protocolo. Tres respostas seguidas
sem confirmacao em `A544` pausam o protocolo por 30 segundos.

## Visor (confirmado no FWM589, 17/11/2026)

- Sem resposta ao `4A`, o aparelho repete `80 4a` cerca de 4 vezes por
  segundo. Depois de `02 4A 82` ele para, pede `52`, `51 A8` e `52`.
- Se o PC responder `52` com falha, o visor fica apagado ate sair e voltar
  ao PC Link (o aparelho fica esperando texto).
- O visor mostra os 8 primeiros caracteres do texto de `40`/`52` e nao rola
  sozinho. O app rola `Titulo - Artista`: janela de N caracteres (padrao 8,
  ajustavel no app), um passo a cada ~0,45 s, pausa de ~1,6 s no inicio; a
  cada passo envia `02 A1 02` e o aparelho busca `40` (cerca de 2,5 por s).
- Depois de cada ativacao o app envia `02 A1 02`, porque um aparelho que ja
  encontrou o PC nao volta a perguntar.

## O que ainda depende de teste em aparelho

- Largura do visor e bits de `A1` nos MCM (o MCM530 anuncia "title display").
- Se os botoes de volume do aparelho passam a enviar `06`/`07` depois do
  handshake, ou se continuam so no amplificador.
