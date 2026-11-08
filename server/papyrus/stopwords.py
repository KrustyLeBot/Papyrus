"""Words too common to say anything about a document (French, a little English), folded: no accents, lower case."""
STOP = set("""
abord afin ainsi alors apres assez aucun aucune aupres auquel aussi autre autres avant avec avez avoir avons beaucoup
cela celle celles celui cependant certain certaine certains ceux chacun chaque chez comme comment contre dans dela depuis
des deux devra devrait doit donc dont donne votre vous vos elle elles encore enfin entre envers environ etaient etait
etant etre eux fait faire faut fois haut ici ils jamais jusqu leur leurs lequel lesquels lors lorsque mais meme memes
moins mon mes notre nos nous parce parmi partir pas pendant peut peuvent plus plusieurs pour pourquoi pourra pourront
puis quand quant que quel quelle quelles quels quelque quelques quoi sans sera seront ses seulement sinon soit sont sous
suis sur tant tel telle telles tels tous tout toute toutes tres trop une uns vers voici voila avez avons aura auront
article articles page pages numero date objet reference references madame monsieur cordialement salutations veuillez
agreer sentiments distingues bonjour cher chere total montant euros euro
the and for are but not you all any can had her was one our out has have this that with from they will would there their
what about which when your said each she how its who been were than them then these some into more other such only over
also after before between because those through during without should could shall
""".split())
