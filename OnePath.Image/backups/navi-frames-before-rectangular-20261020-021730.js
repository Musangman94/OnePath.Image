/* Real ASCII character frames. Design reference: navi-references/quadruped-cat-imagegen.png.
   Four paws beneath a horizontal body; no upright or humanoid poses. */
(() => {
  const frame = (eyes, legs, tail = ')') => [
    '  /\\_/\\                _',
    ' /     \\              / '+tail,
    `| ${eyes} |____________/ /`,
    '(   ^   )             /',
    ' \\__v__/             /',
    '   / / |      / / | /',
    legs,
    ''
  ].join('\n');
  const legs = [
    '  (_(_/|____|(_(_/|_',
    '  (_/(_|____|(_/(_|_',
    '   /(_/|____| /(_/|_',
    '  (_/ /|____|(_/ /|_',
    '  (_(_/|____|(_(_/|_',
    '   /(_/|____| /(_/|_',
    '  (_/(_|____|(_/(_|_',
    '  (_/ /|____|(_/ /|_'
  ];
  const crouch = (eyes, prop = '') => [
    '',
    '  /\\_/\\    _______',
    ' /     \\__/       \\',
    `| ${eyes} |           )`,
    '(   ^   )     ___ / '+prop,
    ' \\__v__/_____/   \\___',
    '  (__) (__) (__) (__)~',
    ''
  ].join('\n');
  const sleep = z => [
    '                  '+z,
    '  /\\_/\\    .----.',
    ' /     \\__/      \\',
    '| -   - |         )',
    '(   ^   )    .-- /',
    ' \\_____/____(   /',
    '  (__) (__)  \\___)~',
    ''
  ].join('\n');
  window.ONEPATH_NAVI_FRAMES = Object.freeze({
    idle: [frame('o   o',legs[0]),frame('o   o',legs[0],'~'),frame('-   -',legs[0]),frame('o   o',legs[0]),frame('o   o',legs[0],'~'),frame('o   o',legs[0])],
    walking: legs.map((feet,i)=>frame('o   o',feet,i%2?'~':')')),
    working: [crouch('o   o','[__]'),crouch('o   o','[._]'),crouch('o   o','[_:]'),crouch('o   o','[__]')],
    coffee: [crouch('o   o',' )'),crouch('o   o','[_]o'),crouch('-   -','[_]o'),crouch('o   o',' (')],
    sleeping: [sleep('z'),sleep('z Z'),sleep('Z'),sleep('z')],
    meditating: [crouch('-   -'),crouch('-   -'),crouch('-   -'),crouch('-   -')]
  });
})();
