# Protocolo MCM / Philips Sound Agent 2

Este documento registra a implementação compatível usada pelo Philips USB PC Link
Activator. A análise foi feita sobre o instalador original do MCM e, em especial,
sobre `mc500cpl.exe` (SHA-256
`06f7b24f4327a8479db309904a0e0e265633f16655973fb82052d04b3975318f`)
e `uacb.dll`.

## O que o Sound Agent faz

O áudio PCM não passa pelo Sound Agent. O Windows envia o som pela interface USB
AudioStreaming normal. O Sound Agent mantém uma segunda sessão de controle que
prepara o UAC3553B, anuncia o cliente ao aparelho, libera/configura o caminho
PC Link e responde às solicitações que o MCM envia durante o uso.

O MCM e alguns FWM compartilham `0471:0111` e revisão `0402`; portanto o modelo
não pode ser identificado com segurança apenas pelos descritores USB. O app
mantém uma escolha explícita FWM/FWC ou MCM e salva essa escolha. Selecionar MCM
em um FWM impede a sequência padrão de duas escritas; selecionar FWM/FWC em um
MCM impede a negociação do Sound Agent.

## Transporte USB proprietário

- Escrita: control transfer OUT `bmRequestType=0x40`, `bRequest=0x04`,
  `wValue=0`, com o endereço de memória em `wIndex`.
- Leitura: control transfer IN `bmRequestType=0xC0`, `bRequest=0x05`,
  `wValue=0`, com o endereço de memória em `wIndex`.
- Pacote Sound Agent: grava o quadro inteiro em `A4F4`, grava em `A544` o
  primeiro byte do quadro (quantidade de bytes restantes) e lê `A544` até o MCM
  zerá-lo. Na desmontagem do worker de `uacb.dll`, `A4F4` recebe o ponteiro do
  pacote em `0x1000162E` e `A544` recebe um byte do mesmo ponteiro em
  `0x10001667`.
- Formato do quadro: primeiro byte é a quantidade de bytes restantes. Assim,
  `02 B0 01` possui três bytes no USB.
- Mailbox HID: o primeiro byte é lido de `A4EE`; os bytes seguintes, de `A4C1`.

## Inicialização reproduzida

1. Executa o bloco legado do filtro Philips: `A4EF=01`, `A4F0=FF`, firmware
   de 88 bytes em `A556`, escritas em `A0DE/A0DD/A087/A086` e transferência
   real de comprimento zero em `B08C`.
2. Repete duas vezes: `A4EF=01`, `A4F0=02`, leitura de um byte em `A4C1`.
3. Envia `02 B0 01` por `A4F4/A544` e exige confirmação `A544=00`.
4. Escreve `A535=01`.
5. Mantém a leitura interrupt IN aberta para atender comandos de tempo de
   execução. O canal continua ligado no modo MCM mesmo se os botões de mídia
   forem desativados.

Os bits do estado `B0` são: base `0x01`, painel/interface visível `0x02` e
efeitos opcionais ativos `0x04`.

## Despachante 0x60-0x6F

| Comando | Comportamento original reproduzido |
|---|---|
| `60` | Reservado; consumido sem resposta. |
| `61` | Abre/fecha a interface; o app atualiza o bit `0x02` e reenvia `B0` quando há transição. |
| `62` | Atualiza efeitos e reenvia `B0` com o bit `0x04`. |
| `63` | Ajusta parâmetro de efeito; envia confirmação `B1` quando habilitada pela máscara. |
| `64` | Consulta nome por ID; sem o DSP legado, responde `02 64 81`. |
| `65` | Consulta/seleciona preset; sem o DSP legado, responde `02 65 81`. |
| `66` | Ajusta banda do equalizador; envia `B1` se o bit 7 da máscara estiver ativo. |
| `67` | Consulta nome/painel atual; responde `02 64 81` sem o DSP. |
| `68` | Consulta preset atual; responde `02 65 81` sem o DSP. |
| `69` | Consulta parâmetros DSP atuais; responde `02 69 81` sem o DSP. |
| `6A` | Consulta as dez bandas de EQ; responde `02 6A 81` sem o DSP. |
| `6B` | Escreve `A535=01` e responde `03 6B A1 01`. |
| `6C` | Responde `02 6C 82`. |
| `6D` | Responde `06 6D 82 03 04 05 06`. |
| `6E` | Reservado; consumido sem resposta. |
| `6F` | Atualiza a máscara de confirmações dos parâmetros. |

Os pacotes `0x81` são a resposta de falha usada pelo próprio Sound Agent quando
o componente QSound não fornece aqueles dados. Isso encerra corretamente a
consulta sem instalar os drivers antigos `psa500.sys`/`QsndEnum.sys`.
Esses drivers implementavam efeitos, presets e equalização virtual; não são o
transporte do áudio PCM e não são apropriados para Windows moderno.

## Relatórios que não são botões

`80 80` é um relatório autônomo de status do aparelho. Ele é filtrado antes
do mapa aprendido e nunca aciona teclas de mídia.

`80 4A` não é status: é `GET_PC_PLAYER1_STATUS`, do protocolo do
PhilipsRemote (MusicMatch). O aparelho repete esse pedido enquanto não
encontra software no PC. A partir da 0.5.0 ele é respondido com `02 4A 82`
(ver `PC_LINK_REMOTE_PROTOCOL.md`). No FWM589 isso faz o aparelho parar de
repetir e passar a pedir o texto do visor. No MCM595, que repetia `80 4A`
sem som na 0.2.0, essa resposta ainda precisa ser testada.

O comando `0x61` pede que a janela do app seja aberta ou escondida. O app também
reenvia `B0` quando esse estado muda; esse detalhe ainda não foi confirmado no
original e não é necessário para liberar o áudio PCM.

## Critério de sucesso e diagnóstico

A ativação MCM só é considerada concluída quando todas as transferências têm o
comprimento esperado e o aparelho limpa `A544` para zero. Timeout, leitura
curta ou escrita curta são falhas, não falso sucesso. Se a ativação falhar, o
botão manual continua disponível e a reativação automática agenda outra tentativa
após 10 segundos, sem exigir que o USB seja reconectado.

Uma falha ao responder a um pedido de tempo de execução é registrada no log e
consome somente aquele relatório; a thread continua lendo os pedidos seguintes.
O log exportado contém a sequência completa, os valores lidos em `A4C1`, a
confirmação `A544`, os comandos recebidos e as respostas enviadas.

A validação automatizada cobre a sequência exata, todos os comandos `60-6F`,
os bits `B0`, os status periódicos e os caminhos de erro. A confirmação final
de liberação de som depende do teste em um MCM físico.
