// index.js
require('dotenv').config();
console.log('> BOT_TOKEN =', JSON.stringify(process.env.BOT_TOKEN));

const { Client, GatewayIntentBits, EmbedBuilder } = require('discord.js');

const TOKEN = process.env.BOT_TOKEN;
if (!TOKEN) {
  console.error('❌ 请在 .env 中配置 BOT_TOKEN');
  process.exit(1);
}

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent
  ]
});

client.once('ready', () => {
  console.log(`✅ Logged in as ${client.user.tag}`);
});

client.on('messageCreate', async msg => {
  if (msg.author.bot) return;
  const raw = msg.content.trim();

  if (raw === '!strategy') {
    const embed = new EmbedBuilder()
      .setTitle('Common abyss strategy')
      .setColor(0x1ABC9C)
      .setDescription([
        '**Preparation before abyss start:** [Click here](https://discord.com/channels/1082595320074092574/1363695806841880656/1363733485696716903)',
        '',
        '**Tips of abyss:**',
        '- Avoid shield [Click here](https://discord.com/channels/1082595320074092574/1363695806841880656/1381455399336808489)',
        '- Charge multiple Ninjasu [Click here](https://discord.com/channels/1082595320074092574/1363695806841880656/1373203013031952445)',
        '- Sacrifice [Click here](https://discord.com/channels/1082595320074092574/1363695806841880656/1363733485696716903)',
        '',
        '**What team captains should do:** [Click here](https://discord.com/channels/1082595320074092574/1363695806841880656/1382113056552779887)'
      ].join('\n'));
    return msg.channel.send({ embeds: [embed] });
  }


  if (raw === '!攻略') {
    const embed = new EmbedBuilder()
      .setTitle('通用深淵攻略')
      .setColor(0x1ABC9C)
      .setDescription([
        '**深淵前的準備：** [點這裡](https://discord.com/channels/1082595320074092574/1363734401791430868/1363734640556249188)',
        '',
        '**深淵的出傷技巧：**',
        '- 躲減傷盾 [點這裡](https://discord.com/channels/1082595320074092574/1363734401791430868/1381465507944861697)',
        '- 單角二大 [點這裡](https://discord.com/channels/1082595320074092574/1363734401791430868/1373210571432005684)',
        '- 獻祭策略 [點這裡](https://discord.com/channels/1082595320074092574/1363734401791430868/1363734942915367044)',
        '',
        '**特殊王的打法：**',
        '- 暗之身 [點這裡](https://discord.com/channels/1082595320074092574/1363734401791430868/1381474295032844500)',
        '',
        '**隊長應該做的事情：** [點這裡](https://discord.com/channels/1082595320074092574/1363734401791430868/1370435360697352353)'
      ].join('\n'));
    return msg.channel.send({ embeds: [embed] });
  }
  if (raw === '!戦略') {
    const embed = new EmbedBuilder()
      .setTitle('共通深淵戦略')
      .setColor(0x1ABC9C)
      .setDescription([
        '**深淵開始前の準備：** [こちら](https://discord.com/channels/1082595320074092574/1381483438640730242/1382550116833165412)',
        '',
        '**深淵のヒント：**',
        '- シールドを避ける [こちら](https://discord.com/channels/1082595320074092574/1381483438640730242/1382577816398204978)',
        '- 複数回数の忍術 [こちら](https://discord.com/channels/1082595320074092574/1381483438640730242/1382566525382168646)',
        '- 深淵犠牲 [こちら](https://discord.com/channels/1082595320074092574/1381483438640730242/1382557150987030548)',
        '',
        '**キャプテンがやるべきこと：** [こちら](https://discord.com/channels/1082595320074092574/1381483438640730242/1382588080594227220)'
      ].join('\n'));
  
    return msg.channel.send({ embeds: [embed] });
  }
  


  const m = raw.match(/^!(\d+)(?:\s*(en|cn|jp))?$/i);
  if (m) {
    const key  = m[1];                    // 层数
    const lang = m[2]?.toLowerCase() || 'en';
    const strategies = {
      '57': {
        en: {
          titleSuffix: 'Abyss Strategy for 57',
          goal: 57,
          link: 'https://discord.com/channels/1082595320074092574/1363695806841880656/1381677003136176293'
        },
        cn: {
          titleSuffix: '57層深淵攻略',
          goal: 57,
          link: 'https://discord.com/channels/1082595320074092574/1363734401791430868/1381672941288296549'
        },
        jp: {
          titleSuffix: '57層の深淵戦略',
          goal: 57,
          link: 'https://discord.com/channels/1082595320074092574/1381483438640730242/1382585667321597963'
        }
      },
      '58': {
        en: {
          titleSuffix: 'Abyss Strategy for 58',
          goal: 58,
          link: 'https://discord.com/channels/1082595320074092574/1363695806841880656/1381491555122282576'
        },
        cn: {
          titleSuffix: '58層深淵攻略',
          goal: 58,
          link: 'https://discord.com/channels/1082595320074092574/1363734401791430868/1381483774038114466'
        },
        jp: {
          titleSuffix: '58層の深淵戦略',
          goal: 58,
          link: 'https://discord.com/channels/1082595320074092574/1381483438640730242/1382585667321597963'
        }
      }
   
    };

    const cfg = strategies[key]?.[lang];
    if (!cfg) return;

    
    const now  = new Date();
    const diff = 5 - now.getDay();
    const fri  = new Date(now);
    fri.setDate(now.getDate() + diff);
    const fridayStr = `${fri.getMonth()+1}/${fri.getDate()}`;

    const embed = new EmbedBuilder()
      .setTitle(`${fridayStr} ${cfg.titleSuffix}`)
      .setColor(lang === 'cn' ? 0xFF4500 : lang === 'jp' ? 0xFFD700 : 0x1ABC9C)
      .setDescription(
        lang === 'cn'
          ? `**目標：**${cfg.goal}層\n\n**攻略：**[點這裡](${cfg.link})`
        : lang === 'jp'
          ? `**目標：**${cfg.goal}層\n\n**戦略：**[こちら](${cfg.link})`
        : `**Goal:** ${cfg.goal}\n\n**Guide:** [Click here](${cfg.link})`
      );
    return msg.channel.send({ embeds: [embed] });
  }

  
});

client.login(TOKEN);
